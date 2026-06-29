"""The automation agent.

This is the orchestrator. It composes the browser tools and the vision LLM into
a perceive -> think -> act loop:

    1. Take a screenshot of the current page (perceive).
    2. Send it to the model with the goal and ask for the single next action
       (think). The model returns coordinates / text — element detection is done
       visually, not via hardcoded selectors.
    3. Execute that action with the browser tools (act).
    4. Repeat until the model reports the task is finished or we hit a step cap.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from browser_tools import BrowserError, BrowserTools
from config import Config
from llm import LLMError, VisionLLM
from logger import get_logger

log = get_logger()


SYSTEM_PROMPT = """\
You are a web automation agent that controls a real browser by looking at \
screenshots and issuing one action at a time. You behave like a careful human \
using a mouse and keyboard.

You will receive:
- A high-level GOAL.
- The viewport size in pixels.
- A SCREENSHOT of the current page.
- A short history of the actions taken so far.

You are also given a numbered list of the page's INTERACTIVE ELEMENTS (links, \
buttons, inputs) with their true on-screen coordinates. Prefer clicking these by \
index — it is far more reliable than guessing pixels.

Reply with EXACTLY ONE JSON object describing the next action. No prose, no \
markdown — only JSON. Use this schema:

{
  "thought": "one short sentence explaining your reasoning",
  "action": "click_element | click | double_click | send_keys | scroll | done",
  "index": <int, the element number for click_element>,
  "x": <int, pixel X — only for raw click/double_click when no element fits>,
  "y": <int, pixel Y — only for raw click/double_click when no element fits>,
  "text": "<text to type, for send_keys>",
  "press": "<optional special key for send_keys, e.g. Enter, Tab>",
  "clear_first": <true|false, optional for send_keys to clear the field first>,
  "dy": <int, vertical scroll amount for scroll; positive = down>
}

Rules:
- PREFER "click_element" with an "index" from the INTERACTIVE ELEMENTS list.
  Only use raw "click" with x/y when the target is visible but not in the list.
- To fill a text field: first click it (by index), then on the NEXT step use \
"send_keys" with the text. To submit a search, add "press": "Enter".
- Dismiss cookie/consent dialogs first if they block the page (click the \
accept/agree button).
- If the element you need is not visible or not listed, "scroll" to reveal it.
- Coordinates are pixels measured from the top-left of the screenshot/viewport.
- Output "done" as soon as the GOAL is complete and visible — e.g. the fields \
already contain the values, or a success/confirmation message is shown. Do NOT \
repeat an action you have already performed (check ACTIONS SO FAR); if the result \
is already visible, you are done.
- Choose realistic values for any field unless specific values are provided.
"""


class Agent:
    """Drives the browser to satisfy a natural-language goal."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.llm = VisionLLM(api_key=config.api_key, model=config.model)
        self.history: list[str] = []
        self._elements: list[dict] = []  # interactive elements shown this step

    def run(self) -> bool:
        """Execute the full task. Returns True if the agent reported success."""
        with BrowserTools(
            headed=self.config.headed,
            viewport_width=self.config.viewport_width,
            viewport_height=self.config.viewport_height,
            screenshot_dir=self.config.screenshot_dir,
            browser_channel=self.config.browser_channel,
        ) as tools:
            # --- fixed opening moves: open + navigate ---
            tools.open_browser()
            tools.navigate_to_url(self.config.target_url)

            log.info("GOAL: %s", self._goal_text())

            # --- perceive / think / act loop ---
            last_signature = ""
            repeat_count = 0
            for step in range(1, self.config.max_steps + 1):
                log.info("======== step %d/%d ========", step, self.config.max_steps)
                shot = tools.take_screenshot(label=f"step{step}")
                self._elements = tools.get_interactive_elements()
                log.info("perception -> %d interactive elements visible", len(self._elements))

                try:
                    decision = self.llm.decide(
                        system_prompt=SYSTEM_PROMPT,
                        user_text=self._user_prompt(),
                        image_path=shot,
                    )
                except LLMError as exc:
                    log.error("LLM error: %s", exc)
                    return False

                action = str(decision.get("action", "")).lower().strip()
                thought = decision.get("thought", "")
                log.info("THINK: %s", thought)
                log.info("ACT  : %s %s", action, self._args_preview(decision))

                if action == "done":
                    log.info("Agent reports the task is complete.")
                    tools.take_screenshot(label="final")
                    return True

                # Loop guard: if the agent repeats the exact same action several
                # times, it's almost always because the goal is already met but it
                # failed to say "done". Stop and treat it as complete.
                signature = f"{action}|{self._args_preview(decision)}"
                repeat_count = repeat_count + 1 if signature == last_signature else 0
                last_signature = signature
                if repeat_count >= 2:
                    log.info("Same action repeated 3x — assuming the task is complete and stopping.")
                    tools.take_screenshot(label="final")
                    return True

                try:
                    result = self._dispatch(tools, action, decision)
                    self.history.append(f"{action}: {result}")
                except BrowserError as exc:
                    log.warning("Action failed: %s", exc)
                    self.history.append(f"{action}: FAILED ({exc})")

            log.warning("Reached the step limit without an explicit 'done'.")
            tools.take_screenshot(label="final")
            return False

    # -- internals ------------------------------------------------------------

    def _dispatch(self, tools: BrowserTools, action: str, d: Dict[str, Any]) -> str:
        """Map a model decision onto a concrete browser tool call."""
        if action == "click_element":
            idx = int(d["index"])
            if not (0 <= idx < len(self._elements)):
                raise BrowserError(f"Element index {idx} is out of range.")
            el = self._elements[idx]
            return tools.click_on_screen(int(el["x"]), int(el["y"]))
        if action in {"click", "click_on_screen"}:
            return tools.click_on_screen(int(d["x"]), int(d["y"]))
        if action == "double_click":
            return tools.double_click(int(d["x"]), int(d["y"]))
        if action == "send_keys":
            return tools.send_keys(
                text=str(d.get("text") or ""),
                press=str(d.get("press") or ""),
                clear_first=bool(d.get("clear_first") or False),
            )
        if action == "scroll":
            return tools.scroll(dy=int(d.get("dy") or 400), dx=int(d.get("dx") or 0))
        raise BrowserError(f"Unknown action requested by the model: {action!r}")

    def _goal_text(self) -> str:
        goal = self.config.task
        extras = []
        if self.config.form_name:
            extras.append(f'Use "{self.config.form_name}" for the Name field.')
        if self.config.form_description:
            extras.append(f'Use "{self.config.form_description}" for the Description field.')
        if extras:
            goal = goal + " " + " ".join(extras)
        return goal

    def _user_prompt(self) -> str:
        history = "\n".join(f"- {h}" for h in self.history[-8:]) or "- (nothing yet)"
        return (
            f"GOAL: {self._goal_text()}\n"
            f"VIEWPORT: {self.config.viewport_width} x {self.config.viewport_height} pixels\n"
            f"ACTIONS SO FAR:\n{history}\n\n"
            f"INTERACTIVE ELEMENTS (use the index with click_element):\n{self._elements_text()}\n\n"
            "Look at the screenshot and decide the single next action as JSON."
        )

    def _elements_text(self) -> str:
        if not self._elements:
            return "(none detected — scroll or use raw click coordinates)"
        lines = []
        for i, el in enumerate(self._elements):
            kind = el["tag"] + (f"/{el['type']}" if el.get("type") else "")
            label = el["label"] or "(no label)"
            lines.append(f"[{i}] {kind} \"{label}\" @({el['x']},{el['y']})")
        return "\n".join(lines)

    @staticmethod
    def _args_preview(d: Dict[str, Any]) -> str:
        keys = ("index", "x", "y", "text", "press", "clear_first", "dy", "dx")
        shown = {k: d[k] for k in keys if k in d}
        return json.dumps(shown) if shown else ""
