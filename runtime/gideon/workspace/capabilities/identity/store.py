"""Transactional autobiography records with immutable answer revisions."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class ConflictError(ValueError):
    pass


def _fields(prompt: str, theme: str, text: str, parent_id: str | None) -> dict:
    values = {"prompt": prompt, "theme": theme, "text": text}
    for key, limit in (("prompt", 4000), ("theme", 200), ("text", 100000)):
        value = values[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{key} must contain 1..{limit} characters")
    if parent_id is not None and (not isinstance(parent_id, str) or len(parent_id) != 32):
        raise ValueError("parent_id must be a local story identifier or null")
    return {**values, "parent_id": parent_id}


def _revision(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("expected_revision must be a positive integer")


class StoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS stories (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    story_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(story_id, revision));
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
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
    def _get(db, story_id: str) -> dict:
        row = db.execute("SELECT body FROM stories WHERE id=?", (story_id,)).fetchone()
        if row is None:
            raise KeyError("Story not found")
        return json.loads(row[0])

    @classmethod
    def _parent(cls, db, parent_id, story_id=None):
        seen = {story_id}
        while parent_id:
            if parent_id in seen:
                raise ConflictError("A story cannot be its own ancestor")
            seen.add(parent_id)
            parent_id = cls._get(db, parent_id)["parent_id"]

    @staticmethod
    def _save(db, story):
        body = json.dumps(story, ensure_ascii=False)
        db.execute("INSERT OR REPLACE INTO stories VALUES (?, ?)", (story["id"], body))
        db.execute("INSERT INTO revisions VALUES (?, ?, ?)",
                   (story["id"], story["revision"], body))

    def create(self, *, prompt: str, theme: str, text: str,
               parent_id: str | None = None, request_id: str) -> dict:
        fields = _fields(prompt, theme, text, parent_id)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("request_id must contain 1..128 characters")
        fingerprint = json.dumps(fields, sort_keys=True, ensure_ascii=False)
        with self._db() as db:
            prior = db.execute("SELECT fingerprint,response FROM requests WHERE id=?",
                               (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError("request_id was used for a different answer")
                return json.loads(prior[1])
            self._parent(db, parent_id)
            now = datetime.now(timezone.utc).isoformat()
            story = {"id": uuid4().hex, **fields, "created_at": now,
                     "updated_at": now, "revision": 1}
            self._save(db, story)
            db.execute("INSERT INTO requests VALUES (?,?,?)",
                       (request_id, fingerprint, json.dumps(story)))
            return story

    def get(self, story_id: str) -> dict:
        with self._db() as db:
            return self._get(db, story_id)

    def list(self) -> list[dict]:
        with self._db() as db:
            return self._list(db)

    @staticmethod
    def _list(db):
        return sorted((json.loads(row[0]) for row in db.execute("SELECT body FROM stories")),
                      key=lambda story: (story["created_at"], story["id"]))

    def update(self, story_id: str, *, expected_revision: int, prompt: str,
               theme: str, text: str, parent_id: str | None = None) -> dict:
        fields = _fields(prompt, theme, text, parent_id)
        _revision(expected_revision)
        with self._db() as db:
            old = self._get(db, story_id)
            if old["revision"] != expected_revision:
                raise ConflictError("Story changed; reload before saving")
            self._parent(db, parent_id, story_id)
            story = {**old, **fields, "revision": expected_revision + 1,
                     "updated_at": datetime.now(timezone.utc).isoformat()}
            self._save(db, story)
            return story

    def delete(self, story_id: str, *, expected_revision: int) -> None:
        _revision(expected_revision)
        with self._db() as db:
            old = self._get(db, story_id)
            if old["revision"] != expected_revision:
                raise ConflictError("Story changed; reload before deleting")
            if any(s["parent_id"] == story_id for s in self._list(db)):
                raise ConflictError("Delete or reparent follow-ups before this story")
            db.execute("DELETE FROM stories WHERE id=?", (story_id,))

    def chain(self, story_id: str) -> list[dict]:
        with self._db() as db:
            current = self._get(db, story_id)
            while current["parent_id"]:
                current = self._get(db, current["parent_id"])
            stories = self._list(db)
            included = {current["id"]}
            while True:
                expanded = included | {s["id"] for s in stories if s["parent_id"] in included}
                if expanded == included:
                    return [s for s in stories if s["id"] in included]
                included = expanded

    def history(self, story_id: str) -> list[dict]:
        with self._db() as db:
            return self._history(db, story_id)

    @staticmethod
    def _history(db, story_id=None):
        query = "SELECT body FROM revisions"
        args = () if story_id is None else (story_id,)
        if story_id is not None:
            query += " WHERE story_id=?"
        query += " ORDER BY story_id,revision"
        return [json.loads(row[0]) for row in db.execute(query, args)]

    def export(self) -> dict:
        with self._db() as db:
            return {"schema_version": 1, "stories": self._list(db), "history": self._history(db)}
