# Architecture

This document explains how the agent is put together and why the pieces are
shaped the way they are.

## High-level idea

The agent imitates how a person uses a browser: **look, decide, act, repeat.**
A vision language model is the "brain" that looks at a screenshot and decides the
next move; a thin Playwright wrapper is the "hands" that carry it out. Because
decisions are made from pixels rather than from the page's HTML, the agent is not
tied to any particular site's markup — there are no hardcoded selectors.

```
        ┌──────────────────────────────────────────────────┐
        │                      main.py                       │
        │            (config, CLI, orchestration)            │
        └───────────────────────┬──────────────────────────┘
                                 │
                        ┌────────▼─────────┐
                        │     agent.py     │
                        │ perceive→think→act
                        └───┬──────────┬───┘
                  screenshot│          │JSON decision
                            │          │
                  ┌─────────▼───┐  ┌───▼────────────────┐
                  │browser_tools│  │       llm.py       │
                  │ (Playwright)│  │ Gemini ⇄ Groq      │
                  │             │  │ (auto-fallback)    │
                  └─────────────┘  └────────────────────┘
```

## The control loop

`Agent.run()` performs two fixed opening moves (`open_browser`,
`navigate_to_url`) and then enters a bounded loop:

1. **Perceive** — `take_screenshot()` captures the current viewport.
2. **Think** — the screenshot, the goal, the viewport size, and a short history
   of past actions are sent to the model. The model must reply with a single
   JSON action (click / double_click / send_keys / scroll / done) including pixel
   coordinates where relevant.
3. **Act** — `Agent._dispatch()` maps that JSON onto exactly one browser tool.
4. **Repeat** until the model emits `done` or `MAX_STEPS` is reached.

Sending one action at a time (rather than a whole plan up front) lets the agent
react to what actually happened — if a click missed or the page shifted, the next
screenshot reveals it and the model adjusts.

## Module responsibilities

| Module | Responsibility |
|--------|----------------|
| `config.py` | Loads and validates all settings from the environment. One immutable `Config` object flows through the app, so nothing is hardcoded. |
| `logger.py` | A single shared logger writing a colourised console trace and a plain-text log file. |
| `browser_tools.py` | A managed Playwright session exposing only the allowed actions. Each tool is small, validates its inputs, and raises a typed `BrowserError` on failure. |
| `llm.py` | Vision-LLM layer. Each provider (Gemini, Groq) is a small class with one `attempt()` method; `VisionLLM` tries them in priority order and falls back across providers. Both force JSON output for reliable parsing. |
| `agent.py` | The orchestrator that wires perception, reasoning, and action together. |
| `main.py` | CLI parsing, config loading, top-level error handling, exit codes. |

## Key design decisions

- **Vision-based element detection over selectors.** The required toolset
  (`click_on_screen(x, y)`, `send_keys`, `scroll`, …) is coordinate-based by
  design. Letting the model locate elements visually keeps the agent generic: it
  works on a page it has never seen and survives markup changes that would break
  CSS/XPath locators.

- **Free, swappable LLMs with automatic fallback.** Two free providers are
  supported — Google Gemini and Groq (Llama-4 vision) — both over plain REST so
  there is no SDK to drift. Each provider is a tiny class; `VisionLLM` tries them
  in `PROVIDER_ORDER` and, on a rate limit or error, falls through to the next
  provider *immediately* rather than waiting. So an exhausted free-tier quota on
  one provider transparently hands off to the other. Adding a third provider is a
  ~30-line class plus one entry in the catalogue.

- **One action per turn.** This makes the agent self-correcting and the trace
  easy to follow during a demo, at the cost of a few extra model calls.

- **Coordinates == screenshot pixels.** The browser context is created with
  `device_scale_factor = 1` and a fixed viewport, so the coordinates the model
  reads off the screenshot line up with where the mouse actually clicks.

- **Fail soft, log everything.** Tool failures raise a typed error that the loop
  catches, records in history, and feeds back to the model so it can try another
  approach. Network/model errors are surfaced clearly and stop the run cleanly.

## Error handling & safety

- Every browser tool wraps its Playwright call and raises `BrowserError` with a
  clear message; the loop records the failure and continues so a single bad click
  doesn't abort the whole task.
- Click coordinates are validated against the viewport before use.
- `MAX_STEPS` bounds the loop so the agent can never run forever.
- The browser is always torn down via a context manager, even on exceptions.
- Missing/invalid configuration fails fast with an actionable message.

## Possible extensions

- Add a DOM-snapshot tool to cross-check visual coordinates for higher precision.
- Cache the model's view of stable elements to cut the number of calls.
- Add retries with backoff around the LLM call for flaky networks.
- Support form submission and verification of the submitted result.
