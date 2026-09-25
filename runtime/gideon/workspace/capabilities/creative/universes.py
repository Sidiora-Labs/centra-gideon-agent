"""Versioned universe canon and visual identity with pinned library references."""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from .moodboards import BoardStore
from .store import CatalogError, IngredientStore, identifier, integer, keys, text

FIELDS = {"title", "canon", "visual_identity", "ingredient_ids", "board_refs"}


class UniverseStore(IngredientStore):
    def __init__(self, home=None):
        super().__init__(home)
        self.boards = BoardStore(self.home)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS universes(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS universe_revisions(id TEXT, revision INTEGER, record TEXT,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS universe_requests(id TEXT PRIMARY KEY, payload TEXT, record TEXT);
            """)

    def _universe(self, db, id):
        row = db.execute(
            "SELECT record FROM universes WHERE id=?", (identifier(id),)
        ).fetchone()
        if not row:
            raise CatalogError("Universe not found", 404)
        return json.loads(row[0])

    def _values(self, db, value, history=()):
        keys(value, FIELDS)
        canon = value.get("canon", [])
        links = value.get("ingredient_ids", [])
        refs = value.get("board_refs", [])
        if any(
            not isinstance(items, list) or len(items) > cap
            for items, cap in ((canon, 100), (links, 100), (refs, 100))
        ):
            raise CatalogError("Invalid universe collection size")
        entries, seen = [], set()
        for entry in canon:
            keys(entry, {"id", "title", "body"})
            id = identifier(entry.get("id"))
            if id in seen:
                raise CatalogError("Duplicate canon entry")
            seen.add(id)
            entries.append(
                {
                    "id": id,
                    "title": text(entry.get("title"), 200, True),
                    "body": text(entry.get("body", ""), 20000),
                }
            )
        identity = value.get("visual_identity", {})
        keys(identity, {"colors", "style_notes"})
        colors = identity.get("colors", [])
        if (
            not isinstance(colors, list)
            or len(colors) > 20
            or any(
                not isinstance(c, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", c)
                for c in colors
            )
        ):
            raise CatalogError("Colors must be six-digit hex values")
        old_links = {id for row in history for id in row["ingredient_ids"]}
        old_refs = {
            (ref["id"], ref["revision"]) for row in history for ref in row["board_refs"]
        }
        links = list(dict.fromkeys(identifier(id) for id in links))
        for id in links:
            if id not in old_links:
                self._get(db, id)
        normalized = []
        for ref in refs:
            keys(ref, {"id", "revision"})
            pair = (identifier(ref.get("id")), integer(ref.get("revision")))
            if (
                pair not in old_refs
                and not db.execute(
                    "SELECT 1 FROM board_revisions WHERE id=? AND revision=?", pair
                ).fetchone()
            ):
                raise CatalogError("Moodboard revision not found", 404)
            item = {"id": pair[0], "revision": pair[1]}
            if item not in normalized:
                normalized.append(item)
        return {
            "title": text(value.get("title"), 200, True),
            "canon": entries,
            "visual_identity": {
                "colors": [c.lower() for c in colors],
                "style_notes": text(identity.get("style_notes", ""), 20000),
            },
            "ingredient_ids": links,
            "board_refs": normalized,
        }

    def _save(self, db, record):
        encoded = json.dumps(record, sort_keys=True)
        db.execute(
            "INSERT INTO universe_revisions VALUES(?,?,?)",
            (record["id"], record["revision"], encoded),
        )
        db.execute(
            "INSERT OR REPLACE INTO universes VALUES(?,?)", (record["id"], encoded)
        )
        return record

    def create(self, payload):
        keys(payload, FIELDS | {"request_id"})
        request = identifier(payload.get("request_id"))
        body = {k: v for k, v in payload.items() if k in FIELDS}
        encoded = json.dumps(body, sort_keys=True)
        with self.connection() as db:
            prior = db.execute(
                "SELECT payload,record FROM universe_requests WHERE id=?", (request,)
            ).fetchone()
            if prior:
                if prior[0] != encoded:
                    raise CatalogError(
                        "Request ID already used with different values", 409
                    )
                return json.loads(prior[1])
            values = self._values(db, body)
            now = datetime.now(timezone.utc).isoformat()
            record = {
                **values,
                "id": str(uuid4()),
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            self._save(db, record)
            db.execute(
                "INSERT INTO universe_requests VALUES(?,?,?)",
                (request, encoded, json.dumps(record)),
            )
            return record

    def update(self, id, patch):
        keys(patch, FIELDS | {"revision"})
        revision = integer(patch.get("revision"))
        with self.connection() as db:
            old = self._universe(db, id)
            if old["revision"] != revision:
                raise CatalogError("Universe changed; reload before saving", 409)
            history = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT record FROM universe_revisions WHERE id=?", (id,)
                )
            ]
            values = self._values(
                db, {k: patch.get(k, self.editable(old)[k]) for k in FIELDS}, history
            )
            return self._save(
                db,
                {
                    **old,
                    **values,
                    "revision": revision + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    @staticmethod
    def editable(record):
        return {key: record[key] for key in FIELDS}

    def restore(self, id, payload):
        keys(payload, {"revision", "target_revision"})
        target = integer(payload.get("target_revision"))
        record = self.export(id, target)
        return self.update(
            id, {**self.editable(record), "revision": payload.get("revision")}
        )

    def export(self, id, revision=None):
        with self.connection() as db:
            current = self._universe(db, id)
            if revision is None:
                return current
            integer(revision)
            row = db.execute(
                "SELECT record FROM universe_revisions WHERE id=? AND revision=?",
                (id, revision),
            ).fetchone()
            if not row:
                raise CatalogError("Revision not found", 404)
            return json.loads(row[0])

    def get(self, id):
        record = self.export(id)
        with self.connection() as db:
            ingredients = [
                {
                    "id": id,
                    "missing": db.execute(
                        "SELECT 1 FROM ingredients WHERE id=?", (id,)
                    ).fetchone()
                    is None,
                }
                for id in record["ingredient_ids"]
            ]
            boards = [
                {
                    **ref,
                    "missing": db.execute(
                        "SELECT 1 FROM board_revisions WHERE id=? AND revision=?",
                        (ref["id"], ref["revision"]),
                    ).fetchone()
                    is None,
                }
                for ref in record["board_refs"]
            ]
        return {**record, "ingredient_status": ingredients, "board_status": boards}

    def revisions(self, id):
        with self.connection() as db:
            self._universe(db, id)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT record FROM universe_revisions WHERE id=? ORDER BY revision DESC",
                    (id,),
                )
            ]

    def list(self, q="", offset=0, limit=25):
        text(q, 200)
        integer(offset, 0, 1000000)
        integer(limit, 1, 100)
        with self.connection() as db:
            rows = [
                json.loads(r[0])
                for r in db.execute("SELECT record FROM universes ORDER BY id")
            ]
        rows = [r for r in rows if q.casefold() in r["title"].casefold()]
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        }
