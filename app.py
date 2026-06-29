"""Web interface for the Website Automation Agent.

Run it with:

    python app.py

then open http://127.0.0.1:5000 in a browser. Enter a target URL and an
instruction, press Run, and watch the agent's live log and step-by-step
screenshots as it works.

The agent runs in a background thread so the page stays responsive; the browser
front-end polls a small status endpoint for progress.
"""

from __future__ import annotations

import glob
import logging
import os
import threading
from dataclasses import replace
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template_string, request, send_from_directory

from agent import Agent
from config import Config
from logger import get_logger

app = Flask(__name__)
log = get_logger()


class RunState:
    """Shared, thread-safe-ish snapshot of the current/last run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.status = "idle"          # idle | running | success | failed
        self.logs: List[str] = []
        self.error: str = ""
        self.screenshot_dir = "screenshots"


STATE = RunState()


class _ListHandler(logging.Handler):
    """Funnels the agent's log records into the current run's log buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            STATE.logs.append(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


_handler = _ListHandler()
_handler.setLevel(logging.INFO)
_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
log.addHandler(_handler)


def _clear_screenshots(directory: str) -> None:
    for path in glob.glob(os.path.join(directory, "*.png")):
        try:
            os.remove(path)
        except OSError:
            pass


def _run_agent(params: Dict[str, Any]) -> None:
    """Background worker: build config from the form and run the agent."""
    try:
        base = Config.load()
        cfg = replace(
            base,
            target_url=params["url"] or base.target_url,
            task=params["task"] or base.task,
            form_name=params.get("form_name", ""),
            form_description=params.get("form_description", ""),
            max_steps=params.get("max_steps") or base.max_steps,
            headed=params.get("headed", False),
        )
        cfg.validate()
        STATE.screenshot_dir = cfg.screenshot_dir
        _clear_screenshots(cfg.screenshot_dir)

        success = Agent(cfg).run()
        with STATE.lock:
            STATE.status = "success" if success else "failed"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        log.error("Run failed: %s", exc)
        with STATE.lock:
            STATE.error = str(exc)
            STATE.status = "failed"


@app.route("/")
def index() -> str:
    cfg = Config.load()
    return render_template_string(_PAGE, default_url=cfg.target_url, default_task=cfg.task,
                                  default_steps=cfg.max_steps)


@app.route("/run", methods=["POST"])
def run():
    with STATE.lock:
        if STATE.status == "running":
            return jsonify({"started": False, "message": "A run is already in progress."}), 409
        STATE.reset()
        STATE.status = "running"

    data = request.get_json(silent=True) or {}
    params = {
        "url": (data.get("url") or "").strip(),
        "task": (data.get("task") or "").strip(),
        "form_name": (data.get("form_name") or "").strip(),
        "form_description": (data.get("form_description") or "").strip(),
        "headed": bool(data.get("headed", False)),
    }
    try:
        params["max_steps"] = int(data.get("max_steps")) if data.get("max_steps") else 0
    except (TypeError, ValueError):
        params["max_steps"] = 0

    threading.Thread(target=_run_agent, args=(params,), daemon=True).start()
    return jsonify({"started": True})


@app.route("/status")
def status():
    shots = sorted(os.path.basename(p) for p in glob.glob(os.path.join(STATE.screenshot_dir, "*.png")))
    return jsonify({
        "status": STATE.status,
        "logs": STATE.logs[-400:],
        "error": STATE.error,
        "screenshots": [f"/shot/{name}" for name in shots],
    })


@app.route("/shot/<path:name>")
def shot(name: str):
    return send_from_directory(os.path.abspath(STATE.screenshot_dir), os.path.basename(name))


_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website Automation Agent</title>
<style>
  :root { --bg:#0f1117; --panel:#171a23; --line:#262b38; --txt:#e6e8ee; --muted:#9aa3b2; --accent:#5b8cff; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background:var(--bg); color:var(--txt); }
  header { padding:18px 24px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; } header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
  .wrap { display:grid; grid-template-columns: 380px 1fr; gap:0; height: calc(100vh - 70px); }
  .panel { padding:20px; overflow:auto; }
  .left { border-right:1px solid var(--line); }
  label { display:block; font-size:12px; color:var(--muted); margin:14px 0 6px; }
  input[type=text], textarea, input[type=number] { width:100%; background:var(--panel); border:1px solid var(--line);
    color:var(--txt); border-radius:8px; padding:10px 12px; font-size:14px; font-family:inherit; }
  textarea { resize:vertical; min-height:70px; }
  .row { display:flex; gap:12px; } .row > div { flex:1; }
  .check { display:flex; align-items:center; gap:8px; margin-top:14px; color:var(--muted); font-size:13px; }
  button { margin-top:18px; width:100%; background:var(--accent); color:#fff; border:0; border-radius:8px;
    padding:12px; font-size:15px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .status { margin-top:14px; font-size:13px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:20px; font-size:12px; font-weight:600; }
  .idle{background:#2a2f3c;color:#9aa3b2} .running{background:#3a3410;color:#f2c94c}
  .success{background:#10341c;color:#37d67a} .failed{background:#3a1414;color:#f2696a}
  .right h2 { font-size:13px; color:var(--muted); margin:0 0 10px; text-transform:uppercase; letter-spacing:.5px; }
  pre.log { background:#0a0c11; border:1px solid var(--line); border-radius:8px; padding:12px; font-size:12px;
    line-height:1.5; max-height:32vh; overflow:auto; white-space:pre-wrap; word-break:break-word; }
  .shots { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:10px; margin-top:8px; }
  .shots figure { margin:0; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--panel); }
  .shots img { width:100%; display:block; cursor:zoom-in; }
  .shots figcaption { font-size:11px; color:var(--muted); padding:6px 8px; }
</style>
</head>
<body>
<header>
  <h1>Website Automation Agent</h1>
  <p>Enter a target URL and an instruction. The agent will open the page, look at it, and act on its own.</p>
</header>
<div class="wrap">
  <div class="panel left">
    <label>Target URL</label>
    <input id="url" type="text" value="{{ default_url }}" placeholder="https://example.com/form">

    <label>Instruction</label>
    <textarea id="task" placeholder="Describe what the agent should do">{{ default_task }}</textarea>

    <div class="row">
      <div>
        <label>Name value (optional)</label>
        <input id="form_name" type="text" placeholder="leave blank = auto">
      </div>
      <div>
        <label>Max steps</label>
        <input id="max_steps" type="number" value="{{ default_steps }}" min="1" max="40">
      </div>
    </div>

    <label>Description value (optional)</label>
    <input id="form_description" type="text" placeholder="leave blank = auto">

    <label class="check"><input id="headed" type="checkbox" checked> Open a real browser window (your Chrome/Edge) while running</label>

    <button id="runBtn" onclick="startRun()">Run agent</button>
    <div class="status">Status: <span id="badge" class="badge idle">idle</span></div>
    <div id="errbox" class="status" style="color:#f2696a"></div>
  </div>

  <div class="panel right">
    <h2>Live log</h2>
    <pre id="log" class="log">(waiting for a run...)</pre>
    <h2 style="margin-top:18px;">Screenshots</h2>
    <div id="shots" class="shots"></div>
  </div>
</div>

<script>
let polling = null;

async function startRun() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  document.getElementById('errbox').textContent = '';
  const payload = {
    url: document.getElementById('url').value,
    task: document.getElementById('task').value,
    form_name: document.getElementById('form_name').value,
    form_description: document.getElementById('form_description').value,
    max_steps: document.getElementById('max_steps').value,
    headed: document.getElementById('headed').checked,
  };
  const res = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  if (!res.ok) {
    const j = await res.json().catch(()=>({message:'Could not start.'}));
    document.getElementById('errbox').textContent = j.message || 'Could not start.';
    btn.disabled = false;
    return;
  }
  if (!polling) polling = setInterval(poll, 1200);
  poll();
}

function setBadge(s) {
  const b = document.getElementById('badge');
  b.className = 'badge ' + s;
  b.textContent = s;
}

async function poll() {
  const res = await fetch('/status');
  const d = await res.json();
  setBadge(d.status);
  document.getElementById('log').textContent = d.logs.length ? d.logs.join('\\n') : '(no output yet)';
  const logEl = document.getElementById('log'); logEl.scrollTop = logEl.scrollHeight;

  const shots = document.getElementById('shots');
  shots.innerHTML = '';
  d.screenshots.forEach(src => {
    const fig = document.createElement('figure');
    const img = document.createElement('img');
    img.src = src; img.onclick = () => window.open(src, '_blank');
    const cap = document.createElement('figcaption');
    cap.textContent = src.split('/').pop();
    fig.appendChild(img); fig.appendChild(cap); shots.appendChild(fig);
  });
  if (d.error) document.getElementById('errbox').textContent = d.error;

  if (d.status === 'success' || d.status === 'failed' || d.status === 'idle') {
    document.getElementById('runBtn').disabled = false;
    if (d.status !== 'running' && polling) { clearInterval(polling); polling = null; }
  }
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # threaded=True so /status polls are answered while a run thread is busy.
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
