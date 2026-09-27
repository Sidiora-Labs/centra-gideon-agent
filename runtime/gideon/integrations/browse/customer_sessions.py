"""Durable browser reservations bound to a customer and chat conversation."""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class SessionNotFound(Exception):
    pass


class StaleSessionVersion(Exception):
    pass


class InvalidSessionTransition(Exception):
    pass


@dataclass(frozen=True)
class CustomerBrowserSession:
    id: str
    account_id: str
    owner_id: str
    conversation_id: str
    canonical_key: str
    status: str
    version: int
    created_at: float
    updated_at: float
    control_holder: str = "assistant"

    def public(self) -> dict[str, str | int | float]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "control_holder": self.control_holder,
        }


class CustomerBrowserSessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            prior = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='customer_browser_sessions'").fetchone()
            if prior and "'active'" not in prior[0]:
                db.execute("ALTER TABLE customer_browser_sessions RENAME TO customer_browser_sessions_old")
            db.execute(
                """CREATE TABLE IF NOT EXISTS customer_browser_sessions (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('reserved', 'active', 'closed', 'error')),
                    version INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    control_holder TEXT NOT NULL DEFAULT 'assistant',
                    UNIQUE(account_id, canonical_key)
                )"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(customer_browser_sessions)")}
            if "control_holder" not in columns:
                db.execute("ALTER TABLE customer_browser_sessions ADD COLUMN control_holder TEXT NOT NULL DEFAULT 'assistant'")
            if prior and "'active'" not in prior[0]:
                db.execute("""INSERT INTO customer_browser_sessions
                    (id, account_id, owner_id, conversation_id, canonical_key, status,
                     version, created_at, updated_at)
                    SELECT id, account_id, owner_id, conversation_id, canonical_key, status,
                           version, created_at, updated_at FROM customer_browser_sessions_old""")
                db.execute("DROP TABLE customer_browser_sessions_old")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _session(row: sqlite3.Row) -> CustomerBrowserSession:
        return CustomerBrowserSession(**dict(row))

    @staticmethod
    def _owned(
        row: sqlite3.Row | None, account_id: str, owner_id: str
    ) -> CustomerBrowserSession:
        if row is None or row["account_id"] != account_id or row["owner_id"] != owner_id:
            raise SessionNotFound
        return CustomerBrowserSessionStore._session(row)

    def find_conversation(
        self, account_id: str, owner_id: str, canonical_key: str
    ) -> CustomerBrowserSession | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM customer_browser_sessions WHERE account_id=? AND canonical_key=?",
                (account_id, canonical_key),
            ).fetchone()
            return self._owned(row, account_id, owner_id) if row else None

    def reserve(
        self, account_id: str, owner_id: str, conversation_id: str, canonical_key: str
    ) -> tuple[CustomerBrowserSession, bool]:
        if not account_id or not owner_id or not conversation_id or not canonical_key:
            raise ValueError("account, owner and conversation identity are required")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM customer_browser_sessions WHERE account_id=? AND canonical_key=?",
                (account_id, canonical_key),
            ).fetchone()
            if row:
                return self._owned(row, account_id, owner_id), False
            stamp = time.time()
            session_id = uuid.uuid4().hex
            db.execute(
                """INSERT INTO customer_browser_sessions
                   (id, account_id, owner_id, conversation_id, canonical_key,
                    status, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'reserved', 1, ?, ?)""",
                (session_id, account_id, owner_id, conversation_id, canonical_key, stamp, stamp),
            )
            row = db.execute(
                "SELECT * FROM customer_browser_sessions WHERE id=?", (session_id,)
            ).fetchone()
            return self._session(row), True

    def get(
        self, session_id: str, account_id: str, owner_id: str
    ) -> CustomerBrowserSession:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM customer_browser_sessions WHERE id=?", (session_id,)
            ).fetchone()
            return self._owned(row, account_id, owner_id)

    def transition(
        self, session_id: str, account_id: str, owner_id: str,
        *, expected_version: int, action: str
    ) -> CustomerBrowserSession:
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        if action not in {"close", "reopen", "error", "activate", "takeover", "handback", "touch"}:
            raise ValueError("invalid session action")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM customer_browser_sessions WHERE id=?", (session_id,)
            ).fetchone()
            current = self._owned(row, account_id, owner_id)
            if current.version != expected_version:
                raise StaleSessionVersion
            allowed = {
                "close": {"reserved", "active", "error"},
                "reopen": {"closed", "error"},
                "error": {"reserved", "active"},
                "activate": {"reserved"},
                "takeover": {"active"} if current.control_holder == "assistant" else set(),
                "handback": {"active"} if current.control_holder == "customer" else set(),
                "touch": {"active"},
            }
            if current.status not in allowed[action]:
                raise InvalidSessionTransition
            status = {"close": "closed", "reopen": "reserved", "error": "error", "activate": "active", "takeover": "active", "handback": "active", "touch": "active"}[action]
            holder = "customer" if action == "takeover" else "assistant" if action in {"handback", "reopen"} else current.control_holder
            db.execute(
                """UPDATE customer_browser_sessions
                   SET status=?, control_holder=?, version=version+1, updated_at=? WHERE id=?""",
                (status, holder, time.time(), session_id),
            )
            changed = db.execute(
                "SELECT * FROM customer_browser_sessions WHERE id=?", (session_id,)
            ).fetchone()
            return self._session(changed)
