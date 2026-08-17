"""SQLite access for the mock bank app.

Plain sqlite3 (sync) is used deliberately: this is a small demo app with low
concurrency, and FastAPI runs sync `def` route handlers in a threadpool, so
there's no event-loop-blocking concern. Pydantic is reserved for the artifact
/ replay / result schemas per the project's tech stack; the mock app's own
data access is intentionally plain.
"""

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("MOCK_APP_DB_PATH", "./mock_app/bank.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id),
    account_type TEXT NOT NULL,
    balance_cents INTEGER NOT NULL
);
"""

# Member 99999 is intentionally never seeded — used as the canonical
# not-found case for search / lookup / replay-error testing.
SEED_MEMBERS = [
    (12345, "Dana Whitfield"),
    (67890, "Miguel Torres"),
]

SEED_ACCOUNTS = [
    # id, member_id, account_type, balance_cents
    (1001, 12345, "checking", 250_000),   # $2,500.00
    (1002, 12345, "savings", 500_000),    # $5,000.00
    (2001, 67890, "checking", 15_000),    # $150.00 - intentionally low, for insufficient-funds testing
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connection() -> Generator[sqlite3.Connection]:
    """Yield a connection and guarantee it's closed on exit.

    A bare `with sqlite3.Connection(...) as conn:` does NOT close the
    connection — `Connection.__exit__` only commits/rolls back the current
    transaction. This wrapper adds the close that callers actually want.
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db(reset: bool = False) -> None:
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        already_seeded = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] > 0
        if not already_seeded:
            conn.executemany("INSERT INTO members (id, name) VALUES (?, ?)", SEED_MEMBERS)
            conn.executemany(
                "INSERT INTO accounts (id, member_id, account_type, balance_cents) VALUES (?, ?, ?, ?)",
                SEED_ACCOUNTS,
            )
            conn.commit()


def get_member(conn: sqlite3.Connection, member_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()


def search_members(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    query = query.strip()
    if not query:
        return []
    if query.isdigit():
        rows = conn.execute("SELECT * FROM members WHERE id = ?", (int(query),)).fetchall()
        if rows:
            return rows
    return conn.execute(
        "SELECT * FROM members WHERE name LIKE ? ORDER BY name", (f"%{query}%",)
    ).fetchall()


def get_accounts_for_member(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM accounts WHERE member_id = ? ORDER BY id", (member_id,)
    ).fetchall()


def get_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def update_balance(conn: sqlite3.Connection, account_id: int, new_balance_cents: int) -> None:
    conn.execute(
        "UPDATE accounts SET balance_cents = ? WHERE id = ?", (new_balance_cents, account_id)
    )
    conn.commit()


def transfer_funds(
    conn: sqlite3.Connection, from_account_id: int, to_account_id: int, amount_cents: int
) -> None:
    conn.execute(
        "UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
        (amount_cents, from_account_id),
    )
    conn.execute(
        "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
        (amount_cents, to_account_id),
    )
    conn.commit()


def create_account(
    conn: sqlite3.Connection, member_id: int, account_type: str, initial_balance_cents: int
) -> int:
    cursor = conn.execute(
        "INSERT INTO accounts (member_id, account_type, balance_cents) VALUES (?, ?, ?)",
        (member_id, account_type, initial_balance_cents),
    )
    conn.commit()
    return cursor.lastrowid
