"""Database for روز جملة (the wholesale store), separate from the retail Rose database.

SQLite file at WHOLESALE_DB_PATH (default: data/wholesale.db next to the app).
Tables here are the ones every part needs: trader accounts (customers), their
sessions, admin staff and the audit log. The wholesale tables (ws_*) are created
by app/wholesale_schema.py.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    value = os.getenv("WHOLESALE_DB_PATH")
    path = Path(value) if value else ROOT / "data" / "wholesale.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path or db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    must_change_password INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customer_sessions_customer ON customer_sessions(customer_id);

CREATE TABLE IF NOT EXISTS staff_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner', 'manager')),
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES staff_users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    details TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL
);
"""

# Demo accounts for tests and local trials only (init_db(seed=True)).
SEED_CUSTOMERS = [
    ("أحمد محمود", "01001234567"),
    ("سارة علي", "01112223334"),
    ("محمد حسن", "01225556667"),
]


def init_db(path: Optional[Path] = None, seed: bool = False) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        if seed and not conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
            now = utc_now()
            for name, phone in SEED_CUSTOMERS:
                conn.execute(
                    "INSERT INTO customers(name, phone, created_at, updated_at) VALUES(?, ?, ?, ?)",
                    (name, phone, now, now),
                )
    from .wholesale_schema import migrate_wholesale_schema

    with connect(path) as conn:
        migrate_wholesale_schema(conn)
