"""Durable, topic-keyed decisions for existing due commitments."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gideon.core.config.loader import config_dir


def _db_path() -> Path:
    path = config_dir() / "proactive" / "decisions.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    return path


def _connect() -> sqlite3.Connection:
    path = _db_path()
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    path.chmod(0o600)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS decisions (
        topic TEXT PRIMARY KEY, agent TEXT NOT NULL, text TEXT NOT NULL,
        action TEXT NOT NULL, reason TEXT NOT NULL, policy TEXT NOT NULL,
        destination TEXT NOT NULL, context_json TEXT NOT NULL,
        decided_at TEXT NOT NULL, delivered_at TEXT NOT NULL DEFAULT '',
        dismissed_until TEXT NOT NULL DEFAULT '', approved_at TEXT NOT NULL DEFAULT '',
        run_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
    )""")
    return db


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    value["context"] = json.loads(value.pop("context_json"))
    return value


def get(topic: str) -> dict | None:
    with _connect() as db:
        return _row(db.execute("SELECT * FROM decisions WHERE topic = ?", (topic,)).fetchone())


def recent(limit: int = 20) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM decisions ORDER BY decided_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [_row(row) for row in rows]


def record(topic: str, *, agent: str, text: str, action: str, reason: str,
           policy: str, destination: str, context: dict, now: datetime | None = None) -> dict:
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    with _connect() as db:
        db.execute("""INSERT INTO decisions
            (topic, agent, text, action, reason, policy, destination, context_json, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic) DO UPDATE SET
              action=excluded.action, reason=excluded.reason, policy=excluded.policy,
              destination=excluded.destination, context_json=excluded.context_json,
              decided_at=excluded.decided_at,
              dismissed_until=CASE WHEN decisions.dismissed_until <= excluded.decided_at
                                   THEN '' ELSE decisions.dismissed_until END""",
            (topic, agent, text, action, reason, policy, destination, json.dumps(context), stamp))
    return get(topic)


def mark_delivery(topic: str, *, destination: str = "", error: str = "") -> None:
    with _connect() as db:
        db.execute("UPDATE decisions SET delivered_at = ?, destination = ?, error = ? WHERE topic = ?",
                   (datetime.now(timezone.utc).isoformat() if not error else "", destination, error, topic))


def dismiss(topic: str, *, days: int = 7, now: datetime | None = None) -> dict | None:
    stamp = now or datetime.now(timezone.utc)
    with _connect() as db:
        db.execute("UPDATE decisions SET dismissed_until = ?, action = 'silence', reason = 'dismissed for cooldown' WHERE topic = ?",
                   ((stamp + timedelta(days=days)).isoformat(), topic))
    return get(topic)


def suppressed(row: dict | None, *, now: datetime | None = None) -> bool:
    return bool(row and row.get("dismissed_until") and row["dismissed_until"] > (now or datetime.now(timezone.utc)).isoformat())


def approved_run(topic: str, run_id: str) -> dict | None:
    with _connect() as db:
        db.execute("UPDATE decisions SET approved_at = ?, run_id = ? WHERE topic = ? AND run_id = ''",
                   (datetime.now(timezone.utc).isoformat(), run_id, topic))
    return get(topic)
