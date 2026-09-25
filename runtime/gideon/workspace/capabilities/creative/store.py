"""Revisioned creative ingredients with transactional relationship validation."""

import json
import re
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.core.config.loader import config_dir
from gideon.core.sqlite_compat import sqlite3

TYPES = ("character", "place", "object", "theme", "event", "concept")
FIELDS = {"type", "title", "body", "tags", "source_refs", "relations"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class CatalogError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def keys(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise CatalogError("Unexpected fields or invalid object")


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise CatalogError("Invalid record identifier")
    return value


def integer(value, low=1, high=2**31):
    if type(value) is not int or not low <= value <= high:
        raise CatalogError("Invalid integer")
    return value


def text(value, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise CatalogError("Invalid text length")
    return value.strip() if required else value


def validate(value):
    keys(value, FIELDS)
    if value.get("type") not in TYPES:
        raise CatalogError("Unknown ingredient type")
    result = {"type": value["type"], "title": text(value.get("title"), 200, True),
              "body": text(value.get("body", ""), 100000)}
    for field, cap in (("tags", 32), ("source_refs", 64), ("relations", 128)):
        items = value.get(field, [])
        if not isinstance(items, list) or len(items) > cap:
            raise CatalogError("Invalid " + field)
        normalized = []
        for item in items:
            if field == "tags":
                item = text(item, 64, True).casefold()
            else:
                allowed = {"kind", "id"} if field == "source_refs" else {"kind", "target_id"}
                keys(item, allowed)
                kinds = ("artifact", "knowledge") if field == "source_refs" else ("related", "contains")
                if item.get("kind") not in kinds:
                    raise CatalogError("Invalid reference kind")
                key = "id" if field == "source_refs" else "target_id"
                item = {"kind": item["kind"], key: identifier(item.get(key))}
            if item not in normalized:
                normalized.append(item)
        result[field] = normalized
    return result


class IngredientStore:
    def __init__(self, home=None):
        self.home = Path(home) if home is not None else config_dir()
        self.path = self.home / "capabilities" / "creative" / "catalog.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ingredients(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(id TEXT NOT NULL, revision INTEGER NOT NULL,
                    record TEXT NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, payload TEXT NOT NULL, record TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, db, id):
        row = db.execute("SELECT record FROM ingredients WHERE id=?", (identifier(id),)).fetchone()
        if not row:
            raise CatalogError("Ingredient not found", 404)
        return json.loads(row[0])

    def _relations(self, db, id, record):
        records = {key: json.loads(body) for key, body in db.execute("SELECT id,record FROM ingredients")}
        records[id] = record
        for edge in record["relations"]:
            if edge["target_id"] == id or edge["target_id"] not in records:
                raise CatalogError("Relation target is missing or references itself")
        seen, visiting = set(), set()

        def visit(key):
            if key in visiting:
                raise CatalogError("Contains relations cannot form a cycle")
            if key in seen:
                return
            visiting.add(key)
            for edge in records[key]["relations"]:
                if edge["kind"] == "contains":
                    visit(edge["target_id"])
            visiting.remove(key)
            seen.add(key)

        for key in records:
            visit(key)

    def _write(self, db, record):
        encoded = json.dumps(record, sort_keys=True)
        db.execute("INSERT INTO revisions VALUES(?,?,?)", (record["id"], record["revision"], encoded))
        db.execute("INSERT OR REPLACE INTO ingredients VALUES(?,?)", (record["id"], encoded))
        return record

    def create(self, payload):
        keys(payload, FIELDS | {"request_id"})
        request = identifier(payload.get("request_id"))
        values = validate({k: v for k, v in payload.items() if k in FIELDS})
        encoded = json.dumps(values, sort_keys=True)
        with self.connection() as db:
            prior = db.execute("SELECT payload,record FROM requests WHERE id=?", (request,)).fetchone()
            if prior:
                if prior[0] != encoded:
                    raise CatalogError("Request ID already used with different values", 409)
                return json.loads(prior[1])
            now = datetime.now(timezone.utc).isoformat()
            record = {**values, "id": str(uuid4()), "revision": 1, "created_at": now, "updated_at": now}
            self._relations(db, record["id"], record)
            self._write(db, record)
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request, encoded, json.dumps(record)))
            return record

    def update(self, id, patch):
        keys(patch, FIELDS | {"revision"})
        revision = integer(patch.get("revision"))
        if not set(patch) & FIELDS:
            raise CatalogError("No ingredient changes supplied")
        with self.connection() as db:
            old = self._get(db, id)
            if old["revision"] != revision:
                raise CatalogError("Ingredient changed; reload before saving", 409)
            values = validate({k: patch.get(k, old[k]) for k in FIELDS})
            record = {**old, **values, "revision": revision + 1, "updated_at": datetime.now(timezone.utc).isoformat()}
            self._relations(db, id, record)
            return self._write(db, record)

    def restore(self, id, payload):
        keys(payload, {"revision", "target_revision"})
        revision = integer(payload.get("revision"))
        target = integer(payload.get("target_revision"))
        with self.connection() as db:
            current = self._get(db, id)
            if current["revision"] != revision:
                raise CatalogError("Ingredient changed; reload before restoring", 409)
            row = db.execute("SELECT record FROM revisions WHERE id=? AND revision=?", (id, target)).fetchone()
            if not row:
                raise CatalogError("Revision not found", 404)
            record = {**json.loads(row[0]), "revision": revision + 1, "updated_at": datetime.now(timezone.utc).isoformat()}
            self._relations(db, id, record)
            return self._write(db, record)

    def get(self, id):
        with self.connection() as db:
            return self._get(db, id)

    def revisions(self, id):
        with self.connection() as db:
            self._get(db, id)
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM revisions WHERE id=? ORDER BY revision DESC", (id,))]

    def list(self, q="", type="", tag="", offset=0, limit=25):
        text(q, 200)
        text(tag, 64)
        if type and type not in TYPES:
            raise CatalogError("Unknown ingredient type")
        integer(offset, 0, 1000000)
        integer(limit, 1, 100)
        with self.connection() as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT record FROM ingredients ORDER BY id")]
        rows = [r for r in rows if (not type or r["type"] == type)
                and (not tag or tag.casefold() in r["tags"])
                and (not q or q.casefold() in (r["title"] + " " + r["body"]).casefold())]
        return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}

    def source_status(self, record):
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.cognition.knowledge.store import knowledge_db_path

        result = []
        for source in record["source_refs"]:
            exists = False
            if source["kind"] == "artifact":
                try:
                    exists = NativeArtifactProvider(self.home / "artifacts").get(source["id"]) is not None
                except ValueError:
                    pass
            else:
                path = knowledge_db_path(self.home, create=False)
                if path.exists():
                    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
                        exists = db.execute("SELECT 1 FROM items WHERE id=?", (source["id"],)).fetchone() is not None
            result.append({**source, "missing": not exists})
        return result
