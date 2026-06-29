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
from llm import LLMError, build_llm
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

Reply with EXACTLY ONE JSON object describing the next action. No prose, no \
markdown — only JSON. Use this schema:

{
  "thought": "one short sentence explaining your reasoning",
  "action": "click | double_click | send_keys | scroll | done",
  "x": <int, required for click/double_click — pixel X in the screenshot>,
  "y": <int, required for click/double_click — pixel Y in the screenshot>,
  "text": "<text to type, for send_keys>",
  "press": "<optional special key for send_keys, e.g. Enter, Tab>",
  "clear_first": <true|false, optional for send_keys to clear the field first>,
  "dy": <int, vertical scroll amount for scroll; positive = down>
}

Rules:
- Coordinates MUST be actual pixels within the given viewport, measured from the \
top-left corner of the screenshot.
- To fill a text field: first "click" its centre to focus it, then on the next \
step use "send_keys" with the text.
- If the element you need is not visible, "scroll" to bring it into view.
- Only output "done" once every part of the GOAL has been completed and is \
visible in the screenshot.
- Choose realistic values for any field unless specific values are provided.
"""


class Agent:
    """Drives the browser to satisfy a natural-language goal."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.llm = build_llm(
            gemini_api_key=config.api_key,
            gemini_model=config.model,
            groq_api_key=config.groq_api_key,
            groq_model=config.groq_model,
            provider_order=config.provider_order,
        )
        self.history: list[str] = []

    def run(self) -> bool:
        """Execute the full task. Returns True if the agent reported success."""
        with BrowserTools(
            headed=self.config.headed,
            viewport_width=self.config.viewport_width,
            viewport_height=self.config.viewport_height,
            screenshot_dir=self.config.screenshot_dir,
        ) as tools:
            # --- fixed opening moves: open + navigate ---
            tools.open_browser()
            tools.navigate_to_url(self.config.target_url)

            log.info("GOAL: %s", self._goal_text())

            # --- perceive / think / act loop ---
            for step in range(1, self.config.max_steps + 1):
                log.info("======== step %d/%d ========", step, self.config.max_steps)
                shot = tools.take_screenshot(label=f"step{step}")

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
        if action in {"click", "click_on_screen"}:
            return tools.click_on_screen(int(d["x"]), int(d["y"]))
        if action == "double_click":
            return tools.double_click(int(d["x"]), int(d["y"]))
        if action == "send_keys":
            return tools.send_keys(
                text=str(d.get("text", "")),
                press=str(d.get("press", "")),
                clear_first=bool(d.get("clear_first", False)),
            )
        if action == "scroll":
            return tools.scroll(dy=int(d.get("dy", 400)), dx=int(d.get("dx", 0)))
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
            "Look at the screenshot and decide the single next action as JSON."
        )

    @staticmethod
    def _args_preview(d: Dict[str, Any]) -> str:
        keys = ("x", "y", "text", "press", "clear_first", "dy", "dx")
        shown = {k: d[k] for k in keys if k in d}
        return json.dumps(shown) if shown else ""
