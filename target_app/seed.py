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
    access_denied INTEGER NOT NULL DEFAULT 0
);
"""

RECORDS = [
    # id,      name,            savings_balance, access_denied
    ("10001", "Jane Doe", 4521.10, 0),
    ("10002", "Robert Chen", 1875.32, 1),
    ("10003", "Maria Alvarez", 812.45, 0),
]


def main() -> None:
    DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(SCHEMA)
        conn.executemany(
            "INSERT INTO members (id, name, savings_balance, access_denied) VALUES (?, ?, ?, ?)",
            RECORDS,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Seeded {len(RECORDS)} member records into {DB_PATH}")


if __name__ == "__main__":
    main()
