"""Minimal operator console for human escalation.

Deliberately bare: shows the most recently paused run's status, reason,
and evidence screenshot, with one action — Resume — which flips
`owner` back to `automation` in the shared sessions.db row that
agent.escalation's pause_for_escalation() is polling. No real-time
co-browsing console (explicitly out of scope per the brief); the point
is that the human resumes work in the SAME live browser window the
automation was driving, not a fresh session.

Run: python -m agent.operator_console  (serves http://localhost:8100)
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, send_file, url_for

from agent import escalation

app = Flask(__name__)

_TEMPLATE = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Operator Console</title></head>
<body style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
<h1>Operator Console</h1>
{% if run %}
  <p><strong>Run:</strong> {{ run['run_id'] }}</p>
  <p><strong>Capability:</strong> {{ run['capability_id'] }}</p>
  <p><strong>Paused at step:</strong> {{ run['current_step_id'] }}</p>
  <p><strong>Reason:</strong> {{ run['pause_reason'] }}</p>
  <p><strong>Updated:</strong> {{ run['updated_at'] }}</p>
  {% if run['screenshot_path'] %}
    <img src="{{ url_for('screenshot', run_id=run['run_id']) }}" style="max-width: 100%; border: 1px solid #999;">
  {% endif %}
  <form method="post" action="{{ url_for('resume', run_id=run['run_id']) }}">
    <button type="submit" style="font-size: 16px; padding: 8px 16px;">Resume</button>
  </form>
{% else %}
  <p>No run is currently paused for escalation.</p>
{% endif %}
</body>
</html>
"""


@app.route("/")
def index():
    run = escalation.latest_paused_run()
    return render_template_string(_TEMPLATE, run=run)


@app.route("/screenshot/<run_id>")
def screenshot(run_id: str):
    run = escalation.latest_paused_run()
    if run is None or run["run_id"] != run_id or not run["screenshot_path"]:
        abort(404)
    path = Path(run["screenshot_path"]).resolve()
    if not path.exists():
        abort(404)
    return send_file(path)


@app.route("/resume/<run_id>", methods=["POST"])
def resume(run_id: str):
    escalation.resume(run_id)
    return redirect(url_for("index"))


if __name__ == "__main__":
    # FLASK_RUN_HOST defaults to loopback-only; docker-compose.yml sets it
    # to 0.0.0.0 so the container's published port is actually reachable.
    app.run(host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"), port=8100, debug=True)
