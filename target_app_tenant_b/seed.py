"""Creates and populates target_app_tenant_b/members_b.db.

Deliberately small: a happy-path member is enough to test replay
against the tenant B surface (unseeded IDs already exercise
MEMBER_NOT_FOUND via a genuine zero-row SELECT, same as target_app).

Run directly: python -m target_app_tenant_b.seed
"""

import sqlite3

from target_app_tenant_b.db import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    savings_balance REAL NOT NULL,
    access_denied INTEGER NOT NULL DEFAULT 0,
    account_number TEXT NOT NULL
);
"""

# Different member IDs and data than tenant A on purpose — this is a
# separate institution's member roster, not a copy.
RECORDS = [
    # id,      name,            savings_balance, access_denied, account_number
    ("20001", "Alice Nguyen", 8390.55, 0, "9981774420"),
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
