"""Agent-facing capability catalog + invoke API — the one stretch goal
this project takes on. A catalog of callable capabilities an AI agent
could discover and invoke, backed directly by compiled artifacts.

Demonstration-scale only, deliberately: no auth, no queueing, one
synchronous browser run per invoke, no rate limiting. Building scaling
infrastructure isn't rewarded per the brief — see CLAUDE.md and
REPORT.md's Cuts section for why this stops here.

CAPABILITY_API_HEADED (default "true") controls whether that run is
headed or headless — see CLAUDE.md for why this is the seam a
production deployment would flip, not the browser dependency itself
going away.

Run: python -m agent.capability_api  (serves http://localhost:8200)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

load_dotenv()

from agent import escalation
from agent.approval import is_approved
from agent.engine import InputValidationError, run_capability
from agent.models import Capability

CAPABILITIES_DIR = Path("schema/capabilities")
OPERATOR_CONSOLE_URL = os.environ.get("OPERATOR_CONSOLE_URL", "http://127.0.0.1:8100")
SELF_URL = os.environ.get("CAPABILITY_API_URL", "http://127.0.0.1:8200")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-sonnet-5")
# Optional comma-separated allowlist of capability_ids the chatbot may offer.
# Unset -> the chatbot sees the whole catalog. Set it to scope a demo to one
# family of capabilities (e.g. the MERIDIAN set) so the model can't pick a
# same-sounding capability whose backing system isn't running.
CHAT_CAPABILITIES = [c.strip() for c in os.environ.get("CHAT_CAPABILITIES", "").split(",") if c.strip()]

app = Flask(__name__)


def _headed_default() -> bool:
    return os.environ.get("CAPABILITY_API_HEADED", "true").strip().lower() not in ("0", "false", "no")


def _scan_capabilities() -> dict[str, Capability]:
    """schema/capabilities/ and its immediate subdirectories (e.g.
    schema/capabilities/meridian/) — schema/example_artifact.json stays
    schema documentation, excluded by directory boundary, not inferred.
    Non-capability sibling files (*.approval.json, *.confidence.json,
    *.policy.json) are attempted and skipped on validation failure."""
    catalog: dict[str, Capability] = {}
    for path in sorted(CAPABILITIES_DIR.glob("*.json")) + sorted(CAPABILITIES_DIR.glob("*/*.json")):
        try:
            cap = Capability.load(path)
        except Exception as exc:
            print(f"skipping {path}: {exc}", file=sys.stderr)
            continue
        catalog[cap.capability_id] = cap
    return catalog


@app.route("/capabilities")
def list_capabilities():
    catalog = _scan_capabilities()
    return jsonify(
        {
            "capabilities": [
                {
                    "capability_id": cap.capability_id,
                    "description": cap.description,
                    "version": cap.version,
                    "risk_class": cap.risk_class,
                    "inputs": [
                        {
                            "name": i.name,
                            "type": i.type,
                            "required": i.required,
                            "description": i.description,
                            "pattern": i.pattern,
                        }
                        for i in cap.inputs
                    ],
                    "outputs": [
                        {"name": o.name, "type": o.type, "description": o.description} for o in cap.outputs
                    ],
                }
                for cap in catalog.values()
            ]
        }
    )


@app.route("/capabilities/<capability_id>/invoke", methods=["POST"])
def invoke_capability(capability_id: str):
    catalog = _scan_capabilities()
    capability = catalog.get(capability_id)
    if capability is None:
        return jsonify({"error": f"no capability {capability_id!r} found under {CAPABILITIES_DIR}/"}), 404

    if not is_approved(capability_id):
        return (
            jsonify(
                {
                    "error": (
                        f"capability {capability_id!r} is not approved for unattended invocation "
                        f"(status: draft). Approve it via schema/capabilities/{capability_id}.approval.json "
                        "before invoking through this API."
                    )
                }
            ),
            403,
        )

    params = request.get_json(silent=True)
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return jsonify({"error": "request body must be a JSON object of param name/value pairs"}), 400

    inject = request.args.get("inject")

    try:
        result = run_capability(capability, params, headed=_headed_default(), inject=inject)
    except InputValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # safety net — run_capability already turns most failures into HardFailureResult
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    status_code = 500 if result.status == "hard_failure" else 200
    return jsonify(result.model_dump(mode="json")), status_code


# ---------------------------------------------------------------------------
# Chatbot layer — a thin conversational driver over the invoke API above.
#
# Deliberately minimal: in-memory per-session history (no persistence), one
# Claude tool-use loop per turn, tools generated straight from the capability
# catalog. A tool call is executed by calling this app's OWN
# POST /capabilities/<id>/invoke over HTTP — the same endpoint an external
# agent would hit, not a bypass.
#
# Two pause paths, kept strictly separate:
#   * proactive risk gate (pause_reason == "risk_confirmation_required"):
#     the bot asks the user in plain language; an explicit yes drives the
#     same operator-console /resume the human would click.
#   * reactive escalation (any other pause_reason, e.g. a supervisor-only
#     permission wall): the bot reports it and stops. It never resumes a
#     reactive escalation itself — that needs a human on the operator console.
# ---------------------------------------------------------------------------

_chat_sessions: dict[str, dict] = {}
_chat_client: anthropic.Anthropic | None = None

_CHAT_SYSTEM = """You are a support assistant for bank back-office operators. You can \
complete real operations by calling the capability tools provided. Rules:

- Only call a tool when you have every required parameter. If something required is \
missing, ask the user for it in plain language — never invent values.
- Some capabilities are mutating and pause for explicit human confirmation before they \
run. When a tool result says PAUSED_FOR_CONFIRMATION, stop calling tools and ask the \
user to confirm, in plain language, summarising exactly what will happen.
- When a tool result says NEEDS_SUPERVISOR, tell the user plainly that the operation \
was rejected and that a supervisor must take over via the operator console. Do not \
claim it succeeded and do not try again.
- When a tool returns results, report them to the user clearly and concisely."""

_AFFIRMATIVE = {"yes", "y", "yeah", "yep", "confirm", "confirmed", "proceed", "do it", "go ahead", "approve", "approved", "ok", "okay"}
_NEGATIVE = {"no", "n", "nope", "cancel", "stop", "abort", "don't", "do not"}


def _client() -> anthropic.Anthropic:
    global _chat_client
    if _chat_client is None:
        _chat_client = anthropic.Anthropic()
    return _chat_client


def _sqlite_now(buffer_seconds: int = 3) -> str:
    """A UTC timestamp in SQLite's datetime('now') format, backdated a few
    seconds so a row written a moment before this call still matches a
    `updated_at >= since` scan."""
    ts = datetime.now(timezone.utc).timestamp() - buffer_seconds
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _tools_from_catalog(catalog: dict[str, Capability]) -> list[dict]:
    tools = []
    for cap in catalog.values():
        props, required = {}, []
        for i in cap.inputs:
            json_type = "number" if i.type in ("money", "number", "integer", "float") else "string"
            props[i.name] = {"type": json_type, "description": i.description or f"{i.name} ({i.type})"}
            if i.required:
                required.append(i.name)
        mutating = cap.risk_class != "read_only" or cap.guardrails.requires_confirmation
        desc = cap.description or cap.capability_id
        if mutating:
            desc += " (MUTATING — will pause for human confirmation before running)"
        tools.append(
            {
                "name": cap.capability_id,
                "description": desc,
                "input_schema": {"type": "object", "properties": props, "required": required},
            }
        )
    return tools


def _invoke_over_http(capability_id: str, params: dict) -> tuple[int, dict]:
    body = json.dumps(params).encode()
    req = urllib.request.Request(
        f"{SELF_URL}/capabilities/{capability_id}/invoke",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _resume_via_operator_console(run_id: str) -> None:
    req = urllib.request.Request(f"{OPERATOR_CONSOLE_URL}/resume/{run_id}", data=b"", method="POST")
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:  # operator console not running in this demo setup
        app.logger.warning("operator console /resume failed (%s); flipping ownership directly", exc)
        escalation.resume(run_id)


def _describe_result(capability_id: str, status_code: int, payload: dict) -> str:
    catalog = _scan_capabilities()
    cap = catalog.get(capability_id)
    status = payload.get("status")
    if status == "success":
        return f"SUCCESS. Outputs: {json.dumps(payload.get('outputs', {}))}"
    if status == "business_outcome":
        code = payload.get("outcome_code")
        human = ""
        if cap is not None:
            oc = cap.expected_outcome(code)
            human = f" — {oc.description}" if oc and oc.description else ""
        return f"BUSINESS_OUTCOME: {code}{human} (reached at step {payload.get('step_id')}). This is a legitimate end state, not an error."
    if status == "hard_failure":
        return (
            f"HARD_FAILURE at step {payload.get('step_id')}: expected {payload.get('expected')!r}, "
            f"observed {payload.get('observed')!r}."
        )
    if status_code == 403:
        return f"NEEDS_SUPERVISOR: {payload.get('error')}"
    return f"ERROR ({status_code}): {payload.get('error') or payload}"


def _run_and_watch(capability_id: str, params: dict) -> dict:
    """Kick the invoke off in a background thread, watch sessions.db for a
    pause. Returns a dict the chat loop turns into a tool_result string."""
    holder: dict = {}
    since = _sqlite_now()

    def worker():
        try:
            holder["status_code"], holder["payload"] = _invoke_over_http(capability_id, params)
        except Exception as exc:  # pragma: no cover - defensive
            holder["status_code"], holder["payload"] = 500, {"error": f"{type(exc).__name__}: {exc}"}

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    run_id = None
    while t.is_alive():
        time.sleep(1.0)
        row = escalation.latest_run_since(capability_id, since) if run_id is None else escalation.get_run(run_id)
        if row is None:
            continue
        run_id = row["run_id"]
        if row["status"] == "paused":
            return _classify_pause(capability_id, params, t, holder, row)

    t.join()
    return {
        "kind": "done",
        "text": _describe_result(capability_id, holder["status_code"], holder["payload"]),
    }


def _classify_pause(capability_id, params, thread, holder, row) -> dict:
    reason = row["pause_reason"]
    run_id = row["run_id"]
    if reason == "risk_confirmation_required":
        return {
            "kind": "confirm",
            "run_id": run_id,
            "thread": thread,
            "holder": holder,
            "capability_id": capability_id,
            "params": params,
            "text": (
                f"PAUSED_FOR_CONFIRMATION: '{capability_id}' is a mutating operation and is paused at "
                f"the risk gate before its first action. Parameters: {json.dumps(params)}. "
                "Ask the user to confirm they want to proceed."
            ),
        }
    # reactive escalation — a runtime rejection a supervisor must handle
    return {
        "kind": "reactive",
        "run_id": run_id,
        "text": (
            f"NEEDS_SUPERVISOR: '{capability_id}' paused mid-run (reason: {reason}) and cannot be "
            "resumed by this assistant. A supervisor must take over the live session via the "
            f"operator console ({OPERATOR_CONSOLE_URL}/?run_id={run_id})."
        ),
    }


def _await_after_resume(capability_id: str, thread, holder, run_id: str) -> dict:
    while thread.is_alive():
        time.sleep(1.0)
        row = escalation.get_run(run_id)
        if row is not None and row["status"] == "paused":
            return _classify_pause(capability_id, {}, thread, holder, row)
    thread.join()
    return {
        "kind": "done",
        "text": _describe_result(capability_id, holder["status_code"], holder["payload"]),
    }


def _model_turn(session: dict, tools: list[dict]) -> str:
    """Run the tool-use loop until the model produces a plain text answer."""
    for _ in range(8):
        resp = _client().messages.create(
            model=CHAT_MODEL,
            max_tokens=1024,
            system=_CHAT_SYSTEM,
            tools=tools,
            messages=session["history"],
        )
        session["history"].append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return "".join(b.text for b in resp.content if b.type == "text").strip()

        tool_results = []
        stop_loop = False
        for tu in tool_uses:
            outcome = _run_and_watch(tu.name, dict(tu.input))
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": outcome["text"]}
            )
            if outcome["kind"] == "confirm":
                session["pending"] = outcome
                stop_loop = True
            elif outcome["kind"] == "reactive":
                stop_loop = True
        session["history"].append({"role": "user", "content": tool_results})
        if stop_loop:
            # one more model turn to phrase the confirmation / rejection, no tools
            resp = _client().messages.create(
                model=CHAT_MODEL, max_tokens=1024, system=_CHAT_SYSTEM, messages=session["history"]
            )
            session["history"].append({"role": "assistant", "content": resp.content})
            return "".join(b.text for b in resp.content if b.type == "text").strip()
    return "(stopped: too many tool iterations)"


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    message = (data.get("message") or "").strip()
    if not session_id or not message:
        return jsonify({"error": "session_id and message are required"}), 400

    session = _chat_sessions.setdefault(session_id, {"history": [], "pending": None})
    catalog = _scan_capabilities()
    if CHAT_CAPABILITIES:
        catalog = {k: v for k, v in catalog.items() if k in CHAT_CAPABILITIES}
    tools = _tools_from_catalog(catalog)

    pending = session.get("pending")
    if pending:
        lowered = message.lower().strip(" .!")
        if lowered in _AFFIRMATIVE or any(lowered.startswith(a) for a in _AFFIRMATIVE):
            session["pending"] = None
            session["history"].append({"role": "user", "content": message})
            _resume_via_operator_console(pending["run_id"])
            outcome = _await_after_resume(
                pending["capability_id"], pending["thread"], pending["holder"], pending["run_id"]
            )
            if outcome["kind"] == "confirm":  # unlikely: a second risk gate
                session["pending"] = outcome
            session["history"].append(
                {"role": "user", "content": f"[system] Capability outcome after your confirmation: {outcome['text']}"}
            )
            resp = _client().messages.create(
                model=CHAT_MODEL, max_tokens=1024, system=_CHAT_SYSTEM, messages=session["history"]
            )
            session["history"].append({"role": "assistant", "content": resp.content})
            reply = "".join(b.text for b in resp.content if b.type == "text").strip()
            return jsonify({"reply": reply})
        if lowered in _NEGATIVE or any(lowered.startswith(n) for n in _NEGATIVE):
            session["pending"] = None
            session["history"].append({"role": "user", "content": message})
            note = (
                f"[system] User declined. The run for '{pending['capability_id']}' is still paused at "
                "the risk gate and was NOT resumed; nothing was submitted."
            )
            session["history"].append({"role": "user", "content": note})
            resp = _client().messages.create(
                model=CHAT_MODEL, max_tokens=512, system=_CHAT_SYSTEM, messages=session["history"]
            )
            session["history"].append({"role": "assistant", "content": resp.content})
            return jsonify({"reply": "".join(b.text for b in resp.content if b.type == "text").strip()})
        # ambiguous — let the model ask again
        session["history"].append({"role": "user", "content": message})
        resp = _client().messages.create(
            model=CHAT_MODEL, max_tokens=512, system=_CHAT_SYSTEM, messages=session["history"]
        )
        session["history"].append({"role": "assistant", "content": resp.content})
        return jsonify({"reply": "".join(b.text for b in resp.content if b.type == "text").strip()})

    session["history"].append({"role": "user", "content": message})
    reply = _model_turn(session, tools)
    return jsonify({"reply": reply})


# ---------------------------------------------------------------------------
# Read-only evidence dashboard (Phase 6).
#
# A minimal operator-facing view over what the system has already done:
# the capability catalog, the run history under evidence/runs/, and a
# step-by-step drill-down into any single run (events, timings, screenshots,
# DOM snapshots, final outcome). No framework, same static-page pattern as
# /chat. Strictly read-only — it never calls run_capability() and never
# mutates sessions.db.
# ---------------------------------------------------------------------------

RUNS_DIR = Path("evidence/runs")


def _read_jsonl(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return events


def _parse_run(run_dir: Path) -> dict | None:
    """Turn one evidence/runs/<run_id>/ directory into a metadata dict.
    The directory name IS the run_id (and matches sessions.db's run_id)."""
    log_path = run_dir / "log.jsonl"
    if not log_path.exists():
        return None
    events = _read_jsonl(log_path)
    start = next((e for e in events if e.get("kind") == "run_start"), {})
    end = next((e for e in events if e.get("kind") == "run_end"), {})

    is_discovery = "goal" in start or run_dir.name.startswith("discover")
    outcome_code = None
    for e in events:
        if e.get("kind") == "checkpoint" and e.get("outcome_code"):
            outcome_code = e["outcome_code"]

    timestamps = [e["ts"] for e in events if "ts" in e]
    duration = None
    if len(timestamps) >= 2:
        try:
            duration = (
                datetime.fromisoformat(timestamps[-1]) - datetime.fromisoformat(timestamps[0])
            ).total_seconds()
        except ValueError:
            pass

    return {
        "run_id": run_dir.name,
        "kind": "discovery" if is_discovery else "replay",
        "capability_id": start.get("capability_id") or (start.get("goal") and "(discovery)") or "unknown",
        "goal": start.get("goal"),
        "target": start.get("target"),
        "model": start.get("model"),
        "params": start.get("params"),
        "status": end.get("status") or ("running" if events else "unknown"),
        "outcome_code": outcome_code,
        "timestamp": timestamps[0] if timestamps else None,
        "duration_seconds": round(duration, 2) if duration is not None else None,
        "n_events": len(events),
        "_events": events,
        "_dir": run_dir,
    }


def _session_row(run_id: str) -> dict | None:
    row = escalation.get_run(run_id)
    if row is None:
        return None
    d = dict(row)
    return {
        "owner": d.get("owner"),
        "status": d.get("status"),
        "pause_reason": d.get("pause_reason"),
        "current_step_id": d.get("current_step_id"),
        "screenshot_path": d.get("screenshot_path"),
        "updated_at": d.get("updated_at"),
    }


def _all_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for d in RUNS_DIR.iterdir():
        if not d.is_dir() or d.name.startswith("_test_"):
            continue
        parsed = _parse_run(d)
        if parsed:
            runs.append(parsed)
    runs.sort(key=lambda r: r["timestamp"] or "", reverse=True)
    return runs


def _public(run: dict, *, with_events: bool = False) -> dict:
    sess = _session_row(run["run_id"])
    escalated = bool(sess and (sess["pause_reason"] or sess["status"] == "paused"))
    out = {k: v for k, v in run.items() if not k.startswith("_")}
    out["escalated"] = escalated
    out["session"] = sess
    if out["status"] in ("running", "unknown") and sess and sess["status"] == "paused":
        out["status"] = "paused"
    if with_events:
        screenshots = []
        shot_dir = run["_dir"] / "screenshots"
        if shot_dir.is_dir():
            screenshots = sorted(p.name for p in shot_dir.iterdir() if p.suffix == ".png")
        events = []
        prev_ts = None
        for e in run["_events"]:
            item = dict(e)
            if isinstance(item.get("observation"), str) and len(item["observation"]) > 12000:
                item["observation"] = item["observation"][:12000] + "\n… (truncated)"
            ts = e.get("ts")
            if ts and prev_ts:
                try:
                    item["_dt"] = round(
                        (datetime.fromisoformat(ts) - datetime.fromisoformat(prev_ts)).total_seconds(), 2
                    )
                except ValueError:
                    pass
            prev_ts = ts or prev_ts
            events.append(item)
        out["events"] = events
        out["screenshots"] = screenshots
    return out


@app.route("/api/runs")
def api_runs():
    runs = _all_runs()
    cap = request.args.get("capability")
    kind = request.args.get("kind")
    on_date = request.args.get("date")  # YYYY-MM-DD prefix match on timestamp
    result = []
    for r in runs:
        if cap and r["capability_id"] != cap:
            continue
        if kind and r["kind"] != kind:
            continue
        if on_date and not (r["timestamp"] or "").startswith(on_date):
            continue
        result.append(_public(r))
    return jsonify({"runs": result, "total": len(result)})


@app.route("/api/runs/<run_id>")
def api_run_detail(run_id: str):
    safe = run_id.replace("/", "").replace("..", "")
    run_dir = RUNS_DIR / safe
    if not run_dir.is_dir():
        return jsonify({"error": f"no run {run_id!r} under {RUNS_DIR}/"}), 404
    parsed = _parse_run(run_dir)
    if parsed is None:
        return jsonify({"error": f"run {run_id!r} has no log.jsonl"}), 404
    return jsonify(_public(parsed, with_events=True))


@app.route("/api/runs/<run_id>/screenshots/<name>")
def api_run_screenshot(run_id: str, name: str):
    safe_id = run_id.replace("/", "").replace("..", "")
    safe_name = name.replace("/", "").replace("..", "")
    path = RUNS_DIR / safe_id / "screenshots" / safe_name
    if not path.is_file() or path.suffix != ".png":
        return jsonify({"error": "not found"}), 404
    return Response(path.read_bytes(), mimetype="image/png")


@app.route("/")
@app.route("/app")
@app.route("/chat", methods=["GET"])
@app.route("/dashboard")
def app_page():
    return Response(_APP_PAGE, mimetype="text/html")


_APP_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Capability Console</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}
 h1{margin-bottom:4px}
 #apptabs{margin:12px 0 20px}
 #apptabs button{padding:8px 18px;margin-right:6px;border:1px solid #bbb;background:#f4f4f4;border-radius:6px;cursor:pointer;font-size:15px}
 #apptabs button.active{background:#dae8fc;border-color:#7aa;font-weight:600}
 #tab-chat #log{border:1px solid #ccc;border-radius:8px;padding:12px;height:60vh;overflow-y:auto;background:#fafafa}
 #tab-chat .msg{margin:8px 0;padding:8px 12px;border-radius:8px;white-space:pre-wrap}
 #tab-chat .user{background:#dae8fc;text-align:right}
 #tab-chat .bot{background:#eee}
 #tab-chat #row{display:flex;gap:8px;margin-top:12px}
 #tab-chat #msg{flex:1;padding:8px}
 #tab-chat #row button{padding:8px 16px}
 #tab-dashboard nav{margin:12px 0}
 #tab-dashboard nav button{padding:6px 14px;margin-right:6px;border:1px solid #bbb;background:#f4f4f4;border-radius:6px;cursor:pointer}
 #tab-dashboard nav button.active{background:#dae8fc;border-color:#7aa}
 #tab-dashboard table{border-collapse:collapse;width:100%;font-size:14px}
 #tab-dashboard th,#tab-dashboard td{border:1px solid #ddd;padding:6px 8px;text-align:left}
 #tab-dashboard th{background:#f4f4f4}
 #tab-dashboard tr.run:hover{background:#f0f6ff;cursor:pointer}
 .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
 .success{background:#d5f5d5;color:#1a6b1a}
 .business_outcome{background:#fff3cd;color:#8a6d00}
 .hard_failure{background:#f8d7da;color:#a12}
 .done{background:#e2e3ff;color:#44a}
 .running,.unknown{background:#eee;color:#666}
 .paused{background:#ffe0b2;color:#b35c00}
 .esc{background:#ffe0b2;color:#b35c00;margin-left:4px}
 .filters{margin:10px 0;font-size:14px} .filters input,.filters select{padding:4px;margin-right:8px}
 .ev{border:1px solid #e2e2e2;border-radius:6px;margin:6px 0;padding:8px;font-size:13px;background:#fafafa}
 .ev .k{font-weight:600;color:#335} .ev .dt{color:#999;float:right}
 pre{white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid #eee;padding:6px;max-height:280px;overflow:auto;font-size:12px}
 img.shot{max-width:100%;border:1px solid #ccc;border-radius:4px;margin:6px 0}
 a.back{display:inline-block;margin:8px 0;cursor:pointer;color:#06c}
 .meta{font-size:14px;color:#444;margin:6px 0}
</style></head><body>
<h1>Capability Console</h1>
<div id="apptabs">
 <button data-t="chat" class="active">Chat</button>
 <button data-t="dashboard">Dashboard</button>
</div>

<div id="tab-chat">
<div id="log"></div>
<div id="row"><input id="msg" placeholder="e.g. what's the balance for member 100234" autofocus>
<button onclick="send()">Send</button></div>
</div>

<div id="tab-dashboard" style="display:none">
<div class="meta">Read-only view of capabilities and past discovery / replay runs.</div>
<nav>
 <button data-v="capabilities" class="active">Capabilities</button>
 <button data-v="runs">Run history</button>
</nav>
<div id="view"></div>
</div>

<script>
function showTab(name){
  document.getElementById("tab-chat").style.display = name==="dashboard"?"none":"block";
  document.getElementById("tab-dashboard").style.display = name==="dashboard"?"block":"none";
  document.querySelectorAll("#apptabs button").forEach(b=>b.classList.toggle("active", b.dataset.t===name));
}
document.querySelectorAll("#apptabs button").forEach(b=>b.onclick=()=>showTab(b.dataset.t));

// ---- Chat tab ----
const sid = "sess-" + Math.random().toString(36).slice(2);
const log = document.getElementById("log"), box = document.getElementById("msg");
function add(who, text){const d=document.createElement("div");d.className="msg "+who;d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;}
async function send(){
  const text = box.value.trim(); if(!text) return;
  add("user", text); box.value=""; box.disabled=true;
  add("bot", "…");
  const thinking = log.lastChild;
  try{
    const r = await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({session_id:sid,message:text})});
    const j = await r.json();
    thinking.textContent = j.reply || j.error || "(no reply)";
  }catch(e){ thinking.textContent = "Error: "+e; }
  box.disabled=false; box.focus();
}
box.addEventListener("keydown",e=>{if(e.key==="Enter")send();});

// ---- Dashboard tab ----
const view = document.getElementById("view");
let runsCache = [];

document.querySelectorAll("#tab-dashboard nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#tab-dashboard nav button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  b.dataset.v === "capabilities" ? showCapabilities() : showRuns();
});

function badge(s){return `<span class="badge ${s||'unknown'}">${s||'unknown'}</span>`}

async function showCapabilities(){
  view.innerHTML = "Loading…";
  const j = await (await fetch("/capabilities")).json();
  const rows = j.capabilities.map(c=>`<tr>
    <td><b>${c.capability_id}</b><br><small>v${c.version}</small></td>
    <td>${c.description||""}</td>
    <td>${c.risk_class}</td>
    <td>${c.inputs.map(i=>`${i.name}:${i.type}${i.required?"*":""}`).join("<br>")}</td>
    <td>${c.outputs.map(o=>`${o.name}:${o.type}`).join("<br>")}</td></tr>`).join("");
  view.innerHTML = `<table><thead><tr><th>Capability</th><th>Description</th><th>Risk</th><th>Inputs</th><th>Outputs</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function showRuns(){
  view.innerHTML = "Loading…";
  const j = await (await fetch("/api/runs")).json();
  runsCache = j.runs;
  const caps = [...new Set(runsCache.map(r=>r.capability_id))].sort();
  view.innerHTML = `<div class="filters">
    Capability <select id="fcap"><option value="">all</option>${caps.map(c=>`<option>${c}</option>`).join("")}</select>
    Kind <select id="fkind"><option value="">all</option><option>discovery</option><option>replay</option></select>
    Date <input id="fdate" placeholder="YYYY-MM-DD" size="10">
    <span id="count"></span></div>
  <table><thead><tr><th>Run</th><th>Kind</th><th>Capability</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody id="rtb"></tbody></table>`;
  ["fcap","fkind","fdate"].forEach(id=>document.getElementById(id).oninput = renderRuns);
  renderRuns();
}

function renderRuns(){
  const cap=document.getElementById("fcap").value, kind=document.getElementById("fkind").value, date=document.getElementById("fdate").value;
  const rows = runsCache.filter(r=>
    (!cap||r.capability_id===cap) && (!kind||r.kind===kind) && (!date||(r.timestamp||"").startsWith(date)));
  document.getElementById("count").textContent = `${rows.length} run(s)`;
  document.getElementById("rtb").innerHTML = rows.map(r=>`<tr class="run" onclick="showRun('${r.run_id}')">
    <td>${r.run_id}</td><td>${r.kind}</td><td>${r.capability_id}</td>
    <td>${badge(r.status)}${r.outcome_code?` <small>${r.outcome_code}</small>`:""}${r.escalated?'<span class="badge esc">escalated</span>':""}</td>
    <td>${(r.timestamp||"").replace("T"," ").slice(0,19)}</td><td>${r.duration_seconds!=null?r.duration_seconds+"s":""}</td></tr>`).join("");
}

async function showRun(id){
  view.innerHTML = "Loading…";
  const r = await (await fetch("/api/runs/"+encodeURIComponent(id))).json();
  if(r.error){ view.innerHTML = r.error; return; }
  let h = `<a class="back" onclick="showRuns()">&larr; back to run history</a>
   <h2>${r.run_id}</h2>
   <div class="meta">${r.kind} · ${badge(r.status)} ${r.outcome_code||""} ${r.escalated?'<span class="badge esc">escalated</span>':""}
   · started ${(r.timestamp||"").replace("T"," ").slice(0,19)} · ${r.duration_seconds!=null?r.duration_seconds+"s":""}</div>`;
  if(r.goal) h += `<div class="meta"><b>Goal:</b> ${r.goal}</div>`;
  if(r.target) h += `<div class="meta"><b>Target:</b> ${r.target}</div>`;
  if(r.params) h += `<div class="meta"><b>Params:</b> ${JSON.stringify(r.params)}</div>`;
  if(r.session) h += `<div class="meta"><b>Session:</b> ${JSON.stringify(r.session)}</div>`;
  if(r.screenshots && r.screenshots.length){
    h += `<h3>Screenshots</h3>` + r.screenshots.map(s=>
      `<div><small>${s}</small><br><img class="shot" src="/api/runs/${encodeURIComponent(r.run_id)}/screenshots/${encodeURIComponent(s)}"></div>`).join("");
  }
  h += `<h3>Events (${r.events.length})</h3>`;
  h += r.events.map(e=>{
    let body = "";
    for(const [k,v] of Object.entries(e)){
      if(["ts","kind","_dt"].includes(k)) continue;
      const val = typeof v === "string" ? v : JSON.stringify(v,null,1);
      body += (val && val.length>120) ? `<div><i>${k}:</i><pre>${esc(val)}</pre></div>` : `<div><i>${k}:</i> ${esc(String(val))}</div>`;
    }
    return `<div class="ev"><span class="k">${e.kind}</span>
      <span class="dt">${e._dt!=null?"+"+e._dt+"s":""} ${(e.ts||"").slice(11,19)}</span>${body}</div>`;
  }).join("");
  view.innerHTML = h;
}
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}

showCapabilities();

showTab(location.pathname.indexOf("dashboard") !== -1 ? "dashboard" : "chat");
</script></body></html>"""


if __name__ == "__main__":
    app.run(host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"), port=8200, debug=True, threaded=True)
