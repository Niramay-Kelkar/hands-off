"""Creates and populates target_app/members.db.

Only the genuine, DB-backed business-outcome records live here. The
app-layer fault-injection IDs (10003 slow-load, 10004 interstitial) are
NOT seeded here — see server.py and target_app/README.md.

Run directly: python -m target_app.seed
"""

import sqlite3

from target_app.db import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    savings_balance REAL NOT NULL,
    access_denied INTEGER NOT NULL DEFAULT 0,
    account_number TEXT NOT NULL
);
"""

# account_number: realistic, teller-facing-only data — shown unmasked on the
# detail page (a real internal app would), but never a declared output of
# member_balance_lookup. Exists specifically to exercise evidence_policy's
# redaction against real sensitive data — see agent/redaction.py.
RECORDS = [
    # id,      name,            savings_balance, access_denied, account_number
    ("10001", "Jane Doe", 4521.10, 0, "4471882203"),
    ("10002", "Robert Chen", 1875.32, 1, "5528301147"),
    ("10003", "Maria Alvarez", 812.45, 0, "3392847761"),
]


def main() -> None:
    DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(SCHEMA)
        conn.executemany(
            "INSERT INTO members (id, name, savings_balance, access_denied, account_number) VALUES (?, ?, ?, ?, ?)",
            RECORDS,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Seeded {len(RECORDS)} member records into {DB_PATH}")


if __name__ == "__main__":
    main()
