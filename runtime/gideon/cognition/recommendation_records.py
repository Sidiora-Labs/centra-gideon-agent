"""Durable decisions for actionable recommendations without an existing native owner."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.config.loader import config_dir


class RecommendationRecords:
    """Store owner-scoped recommendation decisions in the bound Gideon home."""

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home if home is not None else config_dir()).resolve()
        self.path = self.home / "recommendation-decisions.sqlite3"
        self.lock_path = self.home / ".recommendation-decisions.lock"
        self.home.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS recommendation_decisions (
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_list_id TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_evidence TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('accepted','dismissed')),
                    edited_prompt TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_kind, source_list_id, source_id)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS pending_recommendation_decisions (
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_list_id TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_evidence TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('accepted','dismissed')),
                    edited_prompt TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(source_kind, source_list_id, source_id)
                )"""
            )
        self._async_lock = asyncio.Lock()

    @asynccontextmanager
    async def exclusive(self):
        """Serialize async decisions across requests and processes without blocking the loop."""
        async with self._async_lock:
            cancelled = threading.Event()

            def acquire():
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                owned = False
                try:
                    while not cancelled.is_set():
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            if cancelled.is_set():
                                fcntl.flock(descriptor, fcntl.LOCK_UN)
                                return None
                            owned = True
                            return descriptor
                        except BlockingIOError:
                            time.sleep(0.02)
                    return None
                finally:
                    if not owned:
                        os.close(descriptor)

            pending = asyncio.create_task(asyncio.to_thread(acquire))
            try:
                descriptor = await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled.set()
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        cancelled.set()
                acquired = pending.result()
                if acquired is not None:
                    fcntl.flock(acquired, fcntl.LOCK_UN)
                    os.close(acquired)
                raise
            if descriptor is None:
                raise asyncio.CancelledError
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        value = dict(row)
        value["source_evidence"] = json.loads(value["source_evidence"])
        return value

    @classmethod
    def get(cls, db, source_kind: str, source_list_id: str, source_id: str):
        return cls.decode(
            db.execute(
                "SELECT * FROM recommendation_decisions WHERE source_kind=? AND source_list_id=? AND source_id=?",
                (source_kind, source_list_id, source_id),
            ).fetchone()
        )

    @classmethod
    def by_request(cls, db, request_id: str):
        return cls.decode(
            db.execute(
                "SELECT * FROM recommendation_decisions WHERE request_id=?",
                (request_id,),
            ).fetchone()
        )

    @classmethod
    def list(cls, db):
        return [
            cls.decode(row)
            for row in db.execute(
                "SELECT * FROM recommendation_decisions ORDER BY updated_at DESC, source_id"
            ).fetchall()
        ]

    @classmethod
    def list_pending(cls, db):
        return [
            cls.decode(row)
            for row in db.execute(
                "SELECT * FROM pending_recommendation_decisions ORDER BY created_at, source_id"
            ).fetchall()
        ]

    @classmethod
    def pending(cls, db, source_kind: str, source_list_id: str, source_id: str):
        row = db.execute(
            "SELECT * FROM pending_recommendation_decisions WHERE source_kind=? AND source_list_id=? AND source_id=?",
            (source_kind, source_list_id, source_id),
        ).fetchone()
        return cls.decode(row)

    @classmethod
    def stage(cls, db, *, source_kind: str, source_id: str,
              source_list_id: str, source_revision: str, source_title: str,
              source_evidence: str, decision: str, edited_prompt: str,
              request_id: str):
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        db.execute(
            """INSERT INTO pending_recommendation_decisions (
                source_kind, source_id, source_list_id, source_revision,
                source_title, source_evidence, decision, edited_prompt,
                request_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_kind, source_id, source_list_id, source_revision,
             source_title, json.dumps(source_evidence, ensure_ascii=False),
             decision, edited_prompt, request_id, now),
        )
        return cls.pending(db, source_kind, source_list_id, source_id)

    @staticmethod
    def clear_pending(db, source_kind: str, source_list_id: str, source_id: str):
        db.execute(
            "DELETE FROM pending_recommendation_decisions WHERE source_kind=? AND source_list_id=? AND source_id=?",
            (source_kind, source_list_id, source_id),
        )

    @staticmethod
    def write(
        db,
        *,
        source_kind: str,
        source_id: str,
        source_list_id: str,
        source_revision: str,
        source_title: str,
        source_evidence: str,
        decision: str,
        edited_prompt: str,
        task_id: str,
        request_id: str,
    ):
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        db.execute(
            """INSERT INTO recommendation_decisions (
                source_kind, source_id, source_list_id, source_revision,
                source_title, source_evidence, decision, edited_prompt,
                task_id, request_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_kind,
                source_id,
                source_list_id,
                source_revision,
                source_title,
                json.dumps(source_evidence, ensure_ascii=False),
                decision,
                edited_prompt,
                task_id,
                request_id,
                now,
            ),
        )
        return RecommendationRecords.get(db, source_kind, source_list_id, source_id)
