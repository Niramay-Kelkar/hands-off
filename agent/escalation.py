"""Session ownership store for human escalation.

One process owns the Playwright browser end to end (headed, so a human
can physically take over the same window) — there is no separate
service and no RPC. Escalation works entirely through a shared SQLite
table: the replay process writes its paused state and polls the same
row until a human (via agent.operator_console) flips ownership back.

Cut, documented for REPORT.md: if the replay process itself dies while
paused, the browser goes with it. A production version would separate
the browser process from the replay/orchestration process so a paused
session survives an orchestrator restart.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SESSIONS_DB_PATH = Path("evidence/sessions/sessions.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'automation',
    status TEXT NOT NULL DEFAULT 'running',
    current_step_id INTEGER,
    pause_reason TEXT,
    screenshot_path TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    SESSIONS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SESSIONS_DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def open_session(run_id: str, capability_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, capability_id, owner, status, updated_at) "
            "VALUES (?, ?, 'automation', 'running', datetime('now'))",
            (run_id, capability_id),
        )
        conn.commit()
    finally:
        conn.close()


def pause_for_escalation(run_id: str, step_id: int, reason: str, screenshot_path: str, *, poll_interval: float = 1.0) -> None:
    """Writes the paused state, then blocks polling the same row until a
    human flips owner back to 'automation' via the operator console."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE runs SET owner='human', status='paused', current_step_id=?, "
            "pause_reason=?, screenshot_path=?, updated_at=datetime('now') WHERE run_id=?",
            (step_id, reason, screenshot_path, run_id),
        )
        conn.commit()
    finally:
        conn.close()

    while True:
        conn = _connect()
        try:
            row = conn.execute("SELECT owner FROM runs WHERE run_id=?", (run_id,)).fetchone()
        finally:
            conn.close()
        if row and row[0] == "automation":
            return
        time.sleep(poll_interval)


def resume(run_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE runs SET owner='automation', status='running', updated_at=datetime('now') WHERE run_id=?",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()


def complete_session(run_id: str, status: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE runs SET status=?, owner='automation', updated_at=datetime('now') WHERE run_id=?",
            (status, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def latest_paused_run() -> sqlite3.Row | None:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE status='paused' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
