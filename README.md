# Website Automation Agent

An intelligent agent that controls a real web browser on its own — it looks at
the page, decides what to do, and fills in forms without any human clicking.
Think of it as a tiny, self-contained version of tools like *Browser Use*.

The agent navigates to a page, visually locates the form fields, and types in
values — all driven by a **free, fast** vision LLM (Groq's Llama-4 vision). It
does **not** rely on hardcoded CSS selectors; element detection happens by
looking at screenshots, exactly like a person would.

It comes with **two ways to drive it**:

- a **web interface** where you type a target URL + an instruction and watch the
  agent work live (`python app.py`), and
- a **command line** runner (`python main.py`).

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
- A free Groq API key — get one in seconds at <https://console.groq.com/keys>

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
# then open .env and paste your GROQ_API_KEY
```

---

## Run — web interface (recommended)

```bash
python app.py
```

Then open <http://127.0.0.1:5000>. Type a **target URL** and an **instruction**,
press **Run agent**, and watch the live log and step-by-step screenshots as the
agent works. Tick "Show the real browser window" to also see Chromium live.

## Run — command line

```bash
python main.py

# Override page / task for a one-off run
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
| `GROQ_API_KEY` | Your free Groq key (required) |
| `GROQ_MODEL` | Groq vision model, e.g. `meta-llama/llama-4-scout-17b-16e-instruct` |
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
├── app.py             # web interface (Flask) — URL + instruction form
├── main.py            # entry point + CLI
├── agent.py           # perceive → think → act loop
├── browser_tools.py   # the 7 browser tools (Playwright wrapper)
├── llm.py             # free vision-LLM client (Groq REST)
├── config.py          # environment-driven settings
├── logger.py          # shared console + file logging
├── requirements.txt
├── .env.example
├── ARCHITECTURE.md    # design notes
└── README.md
```

---

## Troubleshooting

- **`GROQ_API_KEY is not set`** — copy `.env.example` to `.env` and add your key.
- **Browser fails to launch** — run `python -m playwright install chromium`.
- **Agent clicks slightly off** — increase the viewport in `.env`, or point it at
  a page whose form is clearly visible. The agent re-screenshots every step, so it
  can self-correct over a couple of iterations.
- **Rate limited (429)** — Groq's free tier has a tokens-per-minute limit; the
  agent automatically retries with backoff and recovers. Heavy runs may pause a
  few seconds between steps.
