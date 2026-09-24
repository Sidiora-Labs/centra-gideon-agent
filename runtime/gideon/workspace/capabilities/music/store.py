"""Transactional repertoire records referencing canonical artifact versions."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gideon.workspace.artifacts.provider import ArtifactProvider


class DomainError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_input"):
        super().__init__(message)
        self.status, self.code = status, code


def text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise DomainError(f"Invalid {name}")
    return value.strip() if name != "body" else value


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise DomainError(f"Invalid {name}")
    return value


class RepertoireStore:
    def __init__(self, root: Path, artifacts: ArtifactProvider):
        self.root, self.artifacts = Path(root), artifacts
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "repertoire.sqlite3"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, item_id TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL)")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _read(self, db, item_id):
        row = db.execute("SELECT payload FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise DomainError("Repertoire item not found", 404, "not_found")
        return json.loads(row[0])

    def _write(self, db, item):
        db.execute("INSERT OR REPLACE INTO items VALUES (?, ?)", (item["id"], json.dumps(item)))

    def _fields(self, data):
        if not isinstance(data, dict) or set(data) - {"title", "instrument", "body", "attachment_refs", "revision"}:
            raise DomainError("Unknown repertoire fields")
        out = {}
        for key, limit in (("title", 200), ("instrument", 100), ("body", 100000)):
            if key in data:
                out[key] = text(data[key], key, limit, key == "title")
        if "attachment_refs" in data:
            refs = data["attachment_refs"]
            if not isinstance(refs, list) or len(refs) > 30:
                raise DomainError("Invalid attachment references")
            clean = []
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {"slug", "version"}:
                    raise DomainError("An attachment requires slug and version")
                slug = text(ref["slug"], "attachment slug", 200, True)
                version = integer(ref["version"], "attachment version", 1, 1000000)
                if self.artifacts.get(slug, version=version) is None:
                    raise DomainError("Attachment version not found", 404, "attachment_not_found")
                if {"slug": slug, "version": version} not in clean:
                    clean.append({"slug": slug, "version": version})
            out["attachment_refs"] = clean
        return out

    def create(self, data):
        fields = self._fields(data)
        if "title" not in fields or "revision" in data:
            raise DomainError("A title is required; revision is server managed")
        item = dict(id=str(uuid4()), instrument="", body="", attachment_refs=[], stage="new",
                    practice_history=[], due_at=None, ease=2.5, interval=0, repetitions=0,
                    revision=1, rules_version="sm2-v1", **{})
        item.update(fields)
        with self._db() as db:
            self._write(db, item)
        return item

    def get(self, item_id):
        with self._db() as db:
            return self._read(db, item_id)

    def list(self, *, offset=0, limit=50):
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 100)
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM items ORDER BY id LIMIT ? OFFSET ?", (limit, offset))]

    def update(self, item_id, data):
        fields = self._fields(data)
        revision = integer(data.get("revision"), "revision", 1, 1000000000)
        with self._db() as db:
            item = self._read(db, item_id)
            if item["revision"] != revision:
                raise DomainError("Item changed; reload before editing", 409, "revision_conflict")
            item.update(fields)
            item["revision"] += 1
            self._write(db, item)
            return item

    def practice(self, item_id, data):
        if not isinstance(data, dict) or set(data) != {"attempt_id", "grade", "occurred_at", "timezone", "revision"}:
            raise DomainError("Invalid practice fields")
        attempt_id = text(data["attempt_id"], "attempt ID", 100, True)
        grade = integer(data["grade"], "grade", 0, 5)
        revision = integer(data["revision"], "revision", 1, 1000000000)
        try:
            occurred = datetime.fromisoformat(text(data["occurred_at"], "occurred_at", 64))
            zone = ZoneInfo(text(data["timezone"], "timezone", 100, True))
            if occurred.utcoffset() is None:
                raise ValueError("offset required")
            occurred = occurred.astimezone(timezone.utc)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise DomainError("Practice requires an offset timestamp and IANA timezone") from exc
        fingerprint = json.dumps({**data, "item_id": item_id}, sort_keys=True)
        with self._db() as db:
            prior = db.execute("SELECT fingerprint,receipt FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError("Attempt ID already used with different input", 409, "attempt_conflict")
                return {**json.loads(prior[1]), "replayed": True}
            item = self._read(db, item_id)
            if item["revision"] != revision:
                raise DomainError("Item changed; reload before practicing", 409, "revision_conflict")
            history = item["practice_history"]
            if len(history) >= 10000:
                raise DomainError("Practice history limit reached", 409, "history_full")
            if history and occurred < datetime.fromisoformat(history[-1]["occurred_at"]):
                raise DomainError("Practice must follow the previous attempt")
            repetitions = item["repetitions"] + 1 if grade >= 3 else 0
            interval = 1 if repetitions <= 1 else 6 if repetitions == 2 else min(36500, math.floor(item["interval"] * item["ease"] + 0.5))
            ease = round(max(1.3, item["ease"] + 0.1 - (5-grade)*(0.08+(5-grade)*0.02)), 2)
            try:
                due = (occurred.astimezone(zone) + timedelta(days=interval)).astimezone(timezone.utc).isoformat()
            except OverflowError as exc:
                raise DomainError("Practice date exceeds supported schedule") from exc
            attempt = dict(attempt_id=attempt_id, grade=grade, occurred_at=occurred.isoformat(),
                           timezone=str(zone), rules_version="sm2-v1", due_at=due, interval=interval, ease=ease)
            item.update(stage="review" if repetitions >= 2 else "learning", repetitions=repetitions,
                        interval=interval, ease=ease, due_at=due, revision=revision+1)
            history.append(attempt)
            self._write(db, item)
            receipt = {"item": item, "attempt": attempt, "replayed": False}
            db.execute("INSERT INTO attempts VALUES (?,?,?,?)", (attempt_id, item_id, fingerprint, json.dumps(receipt)))
            return receipt
