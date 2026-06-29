# Website Automation Agent

An intelligent agent that controls a real web browser on its own — it looks at
the page, decides what to do, and fills in forms without any human clicking.
Think of it as a tiny, self-contained version of tools like *Browser Use*.

The agent navigates to a page, visually locates the form fields, and types in
values — all driven by a **free** vision LLM. It does **not** rely on hardcoded
CSS selectors; element detection happens by looking at screenshots, exactly like
a person would.

It supports **two free providers with automatic fallback** — Google Gemini and
Groq (Llama-4 vision). If one is rate-limited or errors, the agent instantly
switches to the other, so a free-tier quota never stops a run.

---

## What it does

By default it performs the assignment task:

1. Opens a browser.
2. Navigates to `https://ui.shadcn.com/docs/forms/react-hook-form`.
3. Finds the form fields on the page.
4. Fills in the **Name** and **Description** fields automatically.
5. Stops and saves a final screenshot.

Everything (URL, task, values, viewport) is configurable — nothing about the
target page is baked into the code.

---

## Core capabilities (tools)

Each tool maps to a method in [`browser_tools.py`](browser_tools.py):

| Tool | What it does |
|------|--------------|
| `open_browser` | Launches a Chromium instance |
| `navigate_to_url` | Directs the browser to a URL |
| `take_screenshot` | Captures the current viewport |
| `click_on_screen(x, y)` | Clicks at pixel coordinates |
| `double_click(x, y)` | Double-clicks at pixel coordinates |
| `send_keys` | Types text / presses keys into the focused element |
| `scroll` | Scrolls the page to reveal hidden elements |

These are intentionally coordinate- and keyboard-based, so the agent operates the
page like a human rather than depending on fragile selectors.

---

## Requirements

- Python 3.10+
- At least one free vision-LLM API key (set both for automatic fallback):
  - **Gemini** — <https://aistudio.google.com/apikey>
  - **Groq** — <https://console.groq.com/keys>

---

## Setup

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt

# 3. install the browser Playwright drives
python -m playwright install chromium

# 4. configure
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
# then open .env and paste your GEMINI_API_KEY
```

---

## Run

```bash
python main.py
```

Useful one-off overrides:

```bash
# Run a different page / task without editing .env
python main.py --url "https://example.com/form" --task "Fill in the contact form"

# Force headless (no visible window)
python main.py --headless
```

While it runs you'll see a live trace of the agent's reasoning and each action.
Screenshots of every step are written to `screenshots/`, and a full log to
`logs/agent.log`.

---

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example)):

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Free Gemini key (at least one provider key required) |
| `LLM_MODEL` | Gemini model, e.g. `gemini-2.0-flash` |
| `GROQ_API_KEY` | Free Groq key (enables fallback) |
| `GROQ_MODEL` | Groq vision model, e.g. `meta-llama/llama-4-scout-17b-16e-instruct` |
| `PROVIDER_ORDER` | Try order + fallback chain, e.g. `gemini,groq` or `groq,gemini` |
| `TARGET_URL` | Page to operate on |
| `TASK` | Natural-language goal for the agent |
| `FORM_NAME` / `FORM_DESCRIPTION` | Optional fixed values (blank = agent invents them) |
| `HEADED` | `true` shows the browser, `false` runs headless |
| `VIEWPORT_WIDTH` / `VIEWPORT_HEIGHT` | Browser size; clicks use this pixel space |
| `MAX_STEPS` | Safety cap on agent steps |
| `SCREENSHOT_DIR` | Where screenshots are saved |

---

## Project layout

```
web-automation/
├── main.py            # entry point + CLI
├── agent.py           # perceive → think → act loop
├── browser_tools.py   # the 7 browser tools (Playwright wrapper)
├── llm.py             # vision-LLM client: Gemini + Groq with auto-fallback
├── config.py          # environment-driven settings
├── logger.py          # shared console + file logging
├── requirements.txt
├── .env.example
├── ARCHITECTURE.md    # design notes
└── README.md
```

---

## Troubleshooting

- **No provider key set** — copy `.env.example` to `.env` and add a Gemini
  and/or Groq key.
- **Browser fails to launch** — run `python -m playwright install chromium`.
- **Agent clicks slightly off** — increase the viewport in `.env`, or try a
  stronger model (`LLM_MODEL=gemini-2.5-flash`). The agent re-screenshots every
  step, so it can self-correct over a couple of iterations.
- **Rate limited (429)** — set both provider keys so the agent auto-falls back;
  it also retries with backoff. If a single provider's free quota is exhausted,
  add the other key or wait for the quota window to reset.
