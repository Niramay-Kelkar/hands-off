"""SQLite access for tenant B's member records.

Mirrors target_app/db.py exactly (same schema, same query shape) — the
point of this second app is to vary branding/markup while keeping the
underlying data access identical. See target_app_tenant_b/README.md.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "members_b.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_member(member_id: str) -> sqlite3.Row | None:
    """Real SELECT — zero rows means MEMBER_NOT_FOUND, not a dict miss."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, name, savings_balance, access_denied, account_number FROM members WHERE id = ?",
            (member_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()
