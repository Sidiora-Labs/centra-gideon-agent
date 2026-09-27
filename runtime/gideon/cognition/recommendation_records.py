"""Durable decisions for actionable recommendations without an existing native owner."""

from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
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

    @contextmanager
    def transaction(self):
        with self.lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
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
                fcntl.flock(lock, fcntl.LOCK_UN)

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
