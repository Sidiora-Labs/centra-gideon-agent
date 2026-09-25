"""Ordered inspiration boards referencing immutable canonical artifact versions."""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from gideon.workspace.artifacts.native import NativeArtifactProvider

from .store import CatalogError, IngredientStore, identifier, integer, keys, text

FIELDS = {"title", "groups", "ingredient_ids"}


class BoardStore(IngredientStore):
    def __init__(self, home=None):
        super().__init__(home)
        self.artifacts = NativeArtifactProvider(self.home / "artifacts")
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS boards(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS board_revisions(id TEXT, revision INTEGER, record TEXT,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS board_requests(id TEXT PRIMARY KEY, payload TEXT, record TEXT);
            """)

    def _board(self, db, id):
        row = db.execute(
            "SELECT record FROM boards WHERE id=?", (identifier(id),)
        ).fetchone()
        if not row:
            raise CatalogError("Moodboard not found", 404)
        return json.loads(row[0])

    def _values(self, db, value, history=()):
        keys(value, FIELDS)
        title = text(value.get("title"), 200, True)
        groups, links = value.get("groups", []), value.get("ingredient_ids", [])
        if (
            not isinstance(groups, list)
            or len(groups) > 20
            or not isinstance(links, list)
            or len(links) > 64
        ):
            raise CatalogError("Too many groups or ingredient links")
        previous = {
            c["id"]: c for r in history for g in r["groups"] for c in g["cards"]
        }
        old_links = {link for r in history for link in r["ingredient_ids"]}
        normalized_links = list(dict.fromkeys(identifier(link) for link in links))
        for link in normalized_links:
            if link not in old_links:
                self._get(db, link)
        result, ids, count = [], set(), 0
        for group in groups:
            keys(group, {"id", "title", "cards"})
            gid = identifier(group.get("id"))
            if gid in ids:
                raise CatalogError("Group and card IDs must be unique")
            ids.add(gid)
            cards = group.get("cards", [])
            if not isinstance(cards, list):
                raise CatalogError("Invalid cards")
            output = {
                "id": gid,
                "title": text(group.get("title"), 100, True),
                "cards": [],
            }
            for card in cards:
                count += 1
                if count > 200:
                    raise CatalogError("At most 200 cards are allowed")
                keys(
                    card, {"id", "artifact_id", "artifact_version", "caption", "colors"}
                )
                cid = identifier(card.get("id"))
                if cid in ids:
                    raise CatalogError("Group and card IDs must be unique")
                ids.add(cid)
                slug = identifier(card.get("artifact_id"))
                version = integer(card.get("artifact_version"))
                colors = card.get("colors", [])
                if (
                    not isinstance(colors, list)
                    or len(colors) > 12
                    or any(
                        not isinstance(c, str)
                        or not re.fullmatch(r"#[0-9a-fA-F]{6}", c)
                        for c in colors
                    )
                ):
                    raise CatalogError("Colors must be six-digit hex values")
                old = previous.get(cid)
                if old:
                    if (old["artifact_id"], old["artifact_version"]) != (slug, version):
                        raise CatalogError(
                            "A card's source version is immutable; add a new card"
                        )
                    provenance = old["provenance"]
                else:
                    source = self.artifacts.get(slug, version=version)
                    if source is None:
                        raise CatalogError("Source artifact version is missing", 404)
                    provenance = {
                        "title": source.name,
                        "kind": source.kind,
                        "added_at": datetime.now(timezone.utc).isoformat(),
                    }
                output["cards"].append(
                    {
                        "id": cid,
                        "artifact_id": slug,
                        "artifact_version": version,
                        "caption": text(card.get("caption", ""), 2000),
                        "colors": [c.lower() for c in colors],
                        "provenance": provenance,
                    }
                )
            result.append(output)
        return {"title": title, "groups": result, "ingredient_ids": normalized_links}

    def _save(self, db, record):
        encoded = json.dumps(record, sort_keys=True)
        db.execute(
            "INSERT INTO board_revisions VALUES(?,?,?)",
            (record["id"], record["revision"], encoded),
        )
        db.execute("INSERT OR REPLACE INTO boards VALUES(?,?)", (record["id"], encoded))
        return record

    def create(self, payload):
        keys(payload, FIELDS | {"request_id"})
        request = identifier(payload.get("request_id"))
        body = {k: v for k, v in payload.items() if k in FIELDS}
        encoded = json.dumps(body, sort_keys=True)
        with self.connection() as db:
            prior = db.execute(
                "SELECT payload,record FROM board_requests WHERE id=?", (request,)
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
                "INSERT INTO board_requests VALUES(?,?,?)",
                (request, encoded, json.dumps(record)),
            )
            return record

    def update(self, id, patch):
        keys(patch, FIELDS | {"revision"})
        revision = integer(patch.get("revision"))
        with self.connection() as db:
            old = self._board(db, id)
            if old["revision"] != revision:
                raise CatalogError("Moodboard changed; reload before saving", 409)
            history = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT record FROM board_revisions WHERE id=?", (id,)
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
        return {
            "title": record["title"],
            "ingredient_ids": record["ingredient_ids"],
            "groups": [
                {
                    **g,
                    "cards": [
                        {k: v for k, v in c.items() if k != "provenance"}
                        for c in g["cards"]
                    ],
                }
                for g in record["groups"]
            ],
        }

    def restore(self, id, payload):
        keys(payload, {"revision", "target_revision"})
        target = integer(payload.get("target_revision"))
        record = self.export(id, target)
        return self.update(
            id, {**self.editable(record), "revision": payload.get("revision")}
        )

    def export(self, id, revision=None):
        with self.connection() as db:
            current = self._board(db, id)
            if revision is None:
                return current
            integer(revision)
            row = db.execute(
                "SELECT record FROM board_revisions WHERE id=? AND revision=?",
                (id, revision),
            ).fetchone()
            if not row:
                raise CatalogError("Revision not found", 404)
            return json.loads(row[0])

    def get(self, id):
        record = self.export(id)
        statuses = []
        for group in record["groups"]:
            for card in group["cards"]:
                source = self.artifacts.get(
                    card["artifact_id"], version=card["artifact_version"]
                )
                statuses.append(
                    {
                        "card_id": card["id"],
                        "missing": source is None,
                        "preview_url": (
                            f"/api/artifacts/{card['artifact_id']}/raw?version={card['artifact_version']}"
                            if source and source.kind == "image"
                            else None
                        ),
                    }
                )
        with self.connection() as db:
            links = [
                {
                    "id": id,
                    "missing": db.execute(
                        "SELECT 1 FROM ingredients WHERE id=?", (id,)
                    ).fetchone()
                    is None,
                }
                for id in record["ingredient_ids"]
            ]
        return {**record, "source_status": statuses, "ingredient_status": links}

    def revisions(self, id):
        with self.connection() as db:
            self._board(db, id)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT record FROM board_revisions WHERE id=? ORDER BY revision DESC",
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
                for r in db.execute("SELECT record FROM boards ORDER BY id")
            ]
        rows = [r for r in rows if q.casefold() in r["title"].casefold()]
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        }

    def sources(self, q=""):
        text(q, 200)
        return {
            "items": [
                {"id": a.slug, "title": a.name, "kind": a.kind, "version": a.version}
                for a in self.artifacts.list(q=q)[:100]
            ]
        }
