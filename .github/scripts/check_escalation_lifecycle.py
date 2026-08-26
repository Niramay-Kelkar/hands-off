#!/usr/bin/env python3
"""CI-only helper for replay-smoke-test.yml.

Drives the standout check: an automated regression test for the escalation
lifecycle itself (pause -> operator resume -> completes), not just the
happy path. It:

1. starts `agent.replay --headless` against member_id=10004 (the seeded
   interstitial fixture) with its browser exposed over CDP via
   AGENT_CDP_PORT, same mechanism validated in BUILD_LOG.md's cross-tenant
   escalation testing;
2. polls the shared sessions.db for that run to reach `status="paused"`,
   `pause_reason="on_unrecognized_dialog"`;
3. attaches a second Playwright process to the SAME live browser over CDP
   and dismisses the modal, standing in for a human fixing the page in
   place before resuming — resuming without this step would just re-detect
   the still-open dialog and re-escalate;
4. POSTs to the real `agent.operator_console` `/resume/<run_id>` endpoint;
5. asserts the paused run then completes with `status="success"`.

Exits non-zero (and prints why) on any assertion failure, so the workflow
step fails cleanly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

CAPABILITY_ID = "member_balance_lookup"
CAPABILITY_PATH = "schema/capabilities/member_balance_lookup.compiled.json"
SESSIONS_DB = Path("evidence/sessions/sessions.db")
CDP_PORT = "9223"
OPERATOR_CONSOLE = "http://localhost:8100"
PAUSE_TIMEOUT_S = 30
RESUME_TIMEOUT_S = 30


def fail(message: str) -> NoReturn:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def wait_for_pause(started_at: str) -> dict:
    # sessions.db can carry paused rows left behind by unrelated earlier
    # runs (e.g. a prior local session's escalation test); scope the
    # lookup to rows updated at-or-after this script's own start so a
    # stale row is never mistaken for the run just launched.
    deadline = time.time() + PAUSE_TIMEOUT_S
    while time.time() < deadline:
        if SESSIONS_DB.exists():
            conn = sqlite3.connect(SESSIONS_DB)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runs WHERE capability_id = ? AND status = 'paused' AND updated_at >= ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (CAPABILITY_ID, started_at),
            ).fetchone()
            conn.close()
            if row:
                return dict(row)
        time.sleep(1)
    fail(f"run never reached paused state within {PAUSE_TIMEOUT_S}s")


def dismiss_dialog() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        page = browser.contexts[0].pages[0]
        dialog = page.get_by_role("dialog")
        dialog.wait_for(state="visible", timeout=5000)
        dialog.get_by_role("button", name="Continue").click()


def resume(run_id: str) -> None:
    req = urllib.request.Request(f"{OPERATOR_CONSOLE}/resume/{run_id}", method="POST")
    urllib.request.urlopen(req, timeout=10)


def main() -> int:
    # A few seconds of slack before "now" absorbs any clock skew between
    # this process and wherever sessions.db's own datetime('now') resolves.
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
    env = {**os.environ, "AGENT_CDP_PORT": CDP_PORT}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent.replay",
            "--capability",
            CAPABILITY_PATH,
            "--input",
            "member_id=10004",
            "--headless",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    run = wait_for_pause(started_at)
    print(f"paused: run_id={run['run_id']} step={run['current_step_id']} reason={run['pause_reason']}")
    if run["pause_reason"] != "on_unrecognized_dialog":
        fail(f"expected pause_reason=on_unrecognized_dialog, got {run['pause_reason']!r}")

    dismiss_dialog()
    print("dismissed the modal via a second Playwright process attached over CDP")

    resume(run["run_id"])
    print(f"POSTed {OPERATOR_CONSOLE}/resume/{run['run_id']}")

    try:
        stdout, _ = proc.communicate(timeout=RESUME_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        fail(f"replay did not complete after resume within {RESUME_TIMEOUT_S}s")

    if proc.returncode != 0:
        print(stdout)
        fail(f"replay exited {proc.returncode} after resume")

    result = json.loads(stdout)
    if result.get("status") != "success":
        fail(f"expected status=success after resume, got {result!r}")

    print(f"PASS: 10004 escalation lifecycle -> {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
