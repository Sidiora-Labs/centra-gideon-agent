"""Transactional repertoire records referencing canonical artifact versions."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gideon.workspace.artifacts.provider import ArtifactProvider


class DomainError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_input"):
        super().__init__(message)
        self.status, self.code = status, code


def text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise DomainError(f"Invalid {name}")
    return value if name in {"body", "notation"} else value.strip()


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
            db.execute("CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, item_id TEXT UNIQUE NOT NULL, receipt TEXT NOT NULL)")
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
        return self._public(json.loads(row[0]))

    def _write(self, db, item):
        stored = {key: value for key, value in item.items() if key != "attachment_availability"}
        db.execute("INSERT OR REPLACE INTO items VALUES (?, ?)", (stored["id"], json.dumps(stored)))

    def _public(self, item):
        item = dict(item)
        item.setdefault("artist", "")
        item.setdefault("tags", [])
        item.setdefault("key", "")
        item.setdefault("capo", 0)
        item.setdefault("tuning", "")
        item.setdefault("notation", {"format": "plain", "text": ""})
        item.setdefault("source_url", "")
        item.setdefault("links", [])
        item.setdefault("scroll_duration_seconds", None)
        item.setdefault("attachment_refs", [])
        item.setdefault("last_practiced_at", None)
        item.setdefault("last_grade", None)
        available = []
        for ref in item["attachment_refs"]:
            artifact = self.artifacts.get(ref["slug"], version=ref["version"])
            available.append({**ref, "available": artifact is not None,
                              "name": artifact.name if artifact else "",
                              "kind": artifact.kind if artifact else "",
                              "mime": artifact.mime if artifact else "",
                              "source": artifact.source if artifact else ""})
        item["attachment_availability"] = available
        return item

    def _fields(self, data):
        editable = {"title", "artist", "instrument", "body", "tags", "key", "capo", "tuning",
                    "notation", "source_url", "links", "scroll_duration_seconds", "attachment_refs", "revision"}
        if not isinstance(data, dict) or set(data) - editable:
            raise DomainError("Unknown repertoire fields")
        out = {}
        for key, limit in (("title", 300), ("artist", 300), ("body", 100000),
                           ("key", 20), ("tuning", 40), ("source_url", 2000)):
            if key in data:
                out[key] = text(data[key], key, limit, key == "title")
        if "instrument" in data:
            instrument = text(data["instrument"], "instrument", 32, True).lower()
            if instrument not in {"guitar", "piano", "ukulele", "bass", "voice", "drums", "other"}:
                raise DomainError("Invalid instrument")
            out["instrument"] = instrument
        if "tags" in data:
            if not isinstance(data["tags"], list) or len(data["tags"]) > 50:
                raise DomainError("Invalid tags")
            tags = []
            for value in data["tags"]:
                tag = text(value, "tag", 50, True)
                if tag not in tags:
                    tags.append(tag)
            out["tags"] = tags
        if "capo" in data:
            out["capo"] = integer(data["capo"], "capo", 0, 12)
        if "notation" in data:
            notation = data["notation"]
            if not isinstance(notation, dict) or set(notation) != {"format", "text"}:
                raise DomainError("Notation requires format and text")
            format_name = text(notation["format"], "notation format", 32, True).lower()
            if format_name not in {"chordpro", "tab", "plain", "drum"}:
                raise DomainError("Invalid notation format")
            out["notation"] = {"format": format_name, "text": text(notation["text"], "notation", 200000)}
        if "source_url" in out and out["source_url"]:
            parsed = urlsplit(out["source_url"])
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise DomainError("Invalid source URL")
        if "links" in data:
            if not isinstance(data["links"], list) or len(data["links"]) > 20:
                raise DomainError("Invalid song links")
            links = []
            for link in data["links"]:
                if not isinstance(link, dict) or set(link) != {"type", "id", "label"}:
                    raise DomainError("A song link requires type, id and label")
                link_type = text(link["type"], "link type", 32, True).lower()
                if not link_type[0].isalnum() or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in link_type):
                    raise DomainError("Invalid link type")
                clean = {"type": link_type, "id": text(link["id"], "link ID", 200, True),
                         "label": text(link["label"], "link label", 300)}
                if clean not in links:
                    links.append(clean)
            out["links"] = links
        if "scroll_duration_seconds" in data:
            out["scroll_duration_seconds"] = None if data["scroll_duration_seconds"] is None else integer(
                data["scroll_duration_seconds"], "scroll duration", 15, 3600)
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

    def _schedule(self, data):
        required = {"stage", "ease", "interval", "repetitions", "due_at", "last_practiced_at", "last_grade"}
        if not isinstance(data, dict) or set(data) != required:
            raise DomainError("Import schedule requires all canonical fields")
        stage = text(data["stage"], "stage", 20, True).lower()
        if stage not in {"new", "learning", "review", "learned", "memorized"}:
            raise DomainError("Invalid stage")
        ease = data["ease"]
        if isinstance(ease, bool) or not isinstance(ease, (int, float)) or not math.isfinite(ease) or not 1.3 <= ease <= 5:
            raise DomainError("Invalid ease")
        result = {
            "stage": stage,
            "ease": round(float(ease), 2),
            "interval": integer(data["interval"], "interval", 0, 36500),
            "repetitions": integer(data["repetitions"], "repetitions", 0, 1000000),
        }
        for key in ("due_at", "last_practiced_at"):
            value = data[key]
            if value is not None:
                try:
                    value = text(value, key, 64, True)
                    parsed = datetime.fromisoformat(value)
                    if parsed.utcoffset() is None:
                        raise ValueError("offset required")
                except ValueError as exc:
                    raise DomainError(f"Invalid {key}") from exc
            result[key] = value
        result["last_grade"] = None if data["last_grade"] is None else integer(data["last_grade"], "last_grade", 0, 5)
        return result

    def _timestamp(self, value, name):
        value = text(value, name, 64, True)
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None:
                raise ValueError("offset required")
        except ValueError as exc:
            raise DomainError(f"Invalid {name}") from exc
        return value

    def create(self, data):
        fields = self._fields(data)
        if "title" not in fields or "revision" in data:
            raise DomainError("A title is required; revision is server managed")
        item = dict(id=str(uuid4()), artist="", instrument="guitar", body="", tags=[], key="", capo=0,
                    tuning="", notation={"format": "plain", "text": ""}, source_url="",
                    links=[], scroll_duration_seconds=None, attachment_refs=[], stage="new",
                    practice_history=[], due_at=None, ease=2.5, interval=0, repetitions=0,
                    last_practiced_at=None, last_grade=None,
                    revision=1, rules_version="sm2-v1", **{})
        item.update(fields)
        with self._db() as db:
            self._write(db, item)
        return self._public(item)

    def import_song(self, *, import_id, source_fingerprint, item_id, created_at, updated_at, data, schedule):
        """Atomically restore one stable archive song and retain an idempotency receipt."""
        import_id = text(import_id, "import ID", 200, True)
        source_fingerprint = text(source_fingerprint, "source fingerprint", 200, True)
        item_id = text(item_id, "item ID", 200, True)
        created_at = self._timestamp(created_at, "created_at")
        updated_at = self._timestamp(updated_at, "updated_at")
        if datetime.fromisoformat(updated_at) < datetime.fromisoformat(created_at):
            raise DomainError("updated_at precedes created_at")
        fields = self._fields(data)
        if "title" not in fields or "revision" in data:
            raise DomainError("A title is required; revision is server managed")
        normalized_schedule = self._schedule(schedule)
        fingerprint = json.dumps({"source_fingerprint": source_fingerprint, "item_id": item_id,
                                  "created_at": created_at, "updated_at": updated_at,
                                  "data": fields, "schedule": normalized_schedule}, sort_keys=True)
        with self._db() as db:
            prior = db.execute("SELECT fingerprint,item_id,receipt FROM imports WHERE id=?", (import_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError("Import ID already used with different input", 409, "import_conflict")
                return {**json.loads(prior[2]), "item": self._read(db, prior[1]), "replayed": True}
            if db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                raise DomainError("Repertoire item ID already exists", 409, "item_conflict")
            item = dict(id=item_id, artist="", instrument="guitar", body="", tags=[], key="", capo=0,
                        tuning="", notation={"format": "plain", "text": ""}, source_url="",
                        links=[], scroll_duration_seconds=None, attachment_refs=[], practice_history=[],
                        revision=1, rules_version="sm2-v1",
                        created_at=created_at, updated_at=updated_at, **normalized_schedule)
            item.update(fields)
            self._write(db, item)
            receipt = {"import_id": import_id, "source_fingerprint": source_fingerprint,
                       "item_id": item_id, "revision": 1}
            db.execute("INSERT INTO imports VALUES (?,?,?,?)", (import_id, fingerprint, item_id, json.dumps(receipt)))
            return {**receipt, "item": self._public(item), "replayed": False}

    def rollback_import(self, import_id, source_fingerprint):
        """Delete an unchanged imported song and its receipt for cross-store recovery."""
        import_id = text(import_id, "import ID", 200, True)
        source_fingerprint = text(source_fingerprint, "source fingerprint", 200, True)
        with self._db() as db:
            row = db.execute("SELECT item_id,receipt FROM imports WHERE id=?", (import_id,)).fetchone()
            if not row:
                raise DomainError("Import not found", 404, "import_not_found")
            receipt = json.loads(row[1])
            if receipt["source_fingerprint"] != source_fingerprint:
                raise DomainError("Import fingerprint does not match", 409, "import_conflict")
            item = self._read(db, row[0])
            if item["revision"] != receipt["revision"]:
                raise DomainError("Imported song changed; rollback refused", 409, "revision_conflict")
            db.execute("DELETE FROM items WHERE id=?", (row[0],))
            db.execute("DELETE FROM imports WHERE id=?", (import_id,))
            return {**receipt, "deleted": True}

    def get(self, item_id):
        with self._db() as db:
            return self._read(db, item_id)

    def list(self, *, offset=0, limit=50):
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 100)
        with self._db() as db:
            return [self._public(json.loads(row[0])) for row in db.execute(
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
                        interval=interval, ease=ease, due_at=due, last_practiced_at=occurred.isoformat(),
                        last_grade=grade, revision=revision+1)
            history.append(attempt)
            self._write(db, item)
            receipt = {"item": item, "attempt": attempt, "replayed": False}
            db.execute("INSERT INTO attempts VALUES (?,?,?,?)", (attempt_id, item_id, fingerprint, json.dumps(receipt)))
            return receipt
