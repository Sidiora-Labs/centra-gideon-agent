"""Revisioned literary author profiles with user-authored voice guidance."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from gideon.workspace.artifacts.native import NativeArtifactProvider

from .store import CatalogError, IngredientStore, identifier, integer, keys, text

FIELDS = {"title", "biography", "voice", "sample_refs"}
VOICE_FIELDS = {"perspective", "tense", "tone", "diction", "rhythm", "avoid"}


class AuthorStore(IngredientStore):
    def __init__(self, home=None):
        super().__init__(home)
        self.artifacts = NativeArtifactProvider(self.home / "artifacts")
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS authors(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS author_revisions(id TEXT, revision INTEGER, record TEXT,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS author_requests(id TEXT PRIMARY KEY, payload TEXT, record TEXT);
            """)

    def _author(self, db, id):
        row = db.execute(
            "SELECT record FROM authors WHERE id=?", (identifier(id),)
        ).fetchone()
        if not row:
            raise CatalogError("Author not found", 404)
        return json.loads(row[0])

    def _values(self, db, value, history=()):
        keys(value, FIELDS)
        voice = value.get("voice", {})
        keys(voice, VOICE_FIELDS)
        perspective, tense = voice.get("perspective", "any"), voice.get("tense", "any")
        if perspective not in ("first", "third", "any") or tense not in (
            "past",
            "present",
            "any",
        ):
            raise CatalogError("Invalid voice perspective or tense")
        normalized_voice = {
            "perspective": perspective,
            "tense": tense,
            **{
                key: text(voice.get(key, ""), 4000)
                for key in ("tone", "diction", "rhythm", "avoid")
            },
        }
        refs = value.get("sample_refs", [])
        if not isinstance(refs, list) or len(refs) > 8:
            raise CatalogError("At most eight writing samples are allowed")
        previous = {
            (ref["artifact_id"], ref["artifact_version"])
            for row in history
            for ref in row["sample_refs"]
        }
        normalized = []
        for ref in refs:
            keys(ref, {"artifact_id", "artifact_version"})
            pair = (
                identifier(ref.get("artifact_id")),
                integer(ref.get("artifact_version")),
            )
            artifact = self.artifacts.get(pair[0], version=pair[1])
            if pair not in previous and (
                artifact is None or artifact.kind not in ("markdown", "text")
            ):
                raise CatalogError(
                    "Writing sample must reference an existing text artifact version",
                    404,
                )
            item = {"artifact_id": pair[0], "artifact_version": pair[1]}
            if item not in normalized:
                normalized.append(item)
        return {
            "title": text(value.get("title"), 200, True),
            "biography": text(value.get("biography", ""), 20000),
            "voice": normalized_voice,
            "sample_refs": normalized,
        }

    def _save(self, db, record):
        encoded = json.dumps(record, sort_keys=True)
        db.execute(
            "INSERT INTO author_revisions VALUES(?,?,?)",
            (record["id"], record["revision"], encoded),
        )
        db.execute(
            "INSERT OR REPLACE INTO authors VALUES(?,?)", (record["id"], encoded)
        )
        return record

    def create(self, payload):
        keys(payload, FIELDS | {"request_id"})
        request = identifier(payload.get("request_id"))
        body = {k: v for k, v in payload.items() if k in FIELDS}
        encoded = json.dumps(body, sort_keys=True)
        with self.connection() as db:
            prior = db.execute(
                "SELECT payload,record FROM author_requests WHERE id=?", (request,)
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
                "INSERT INTO author_requests VALUES(?,?,?)",
                (request, encoded, json.dumps(record)),
            )
            return record

    def update(self, id, patch):
        keys(patch, FIELDS | {"revision"})
        revision = integer(patch.get("revision"))
        with self.connection() as db:
            old = self._author(db, id)
            if old["revision"] != revision:
                raise CatalogError("Author changed; reload before saving", 409)
            history = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT record FROM author_revisions WHERE id=?", (id,)
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
            current = self._author(db, id)
            if revision is None:
                return current
            integer(revision)
            row = db.execute(
                "SELECT record FROM author_revisions WHERE id=? AND revision=?",
                (id, revision),
            ).fetchone()
            if not row:
                raise CatalogError("Revision not found", 404)
            return json.loads(row[0])

    def get(self, id):
        record = self.export(id)
        statuses = []
        for ref in record["sample_refs"]:
            artifact = self.artifacts.get(
                ref["artifact_id"], version=ref["artifact_version"]
            )
            statuses.append(
                {
                    **ref,
                    "missing": artifact is None,
                    "title": artifact.name if artifact else ref["artifact_id"],
                }
            )
        return {**record, "sample_status": statuses}

    def sources(self, q=""):
        text(q, 200)
        return {
            "items": [
                {"id": item.slug, "title": item.name, "version": item.version}
                for item in self.artifacts.list(q=q)
                if item.kind in ("markdown", "text")
            ][:100]
        }

    def brief(self, id, revision=None):
        author = self.export(id, revision)
        samples = []
        for ref in author["sample_refs"]:
            artifact = self.artifacts.get(
                ref["artifact_id"], version=ref["artifact_version"]
            )
            content = artifact.content if artifact else ""
            samples.append(
                {
                    **ref,
                    "missing": artifact is None,
                    "title": artifact.name if artifact else ref["artifact_id"],
                    "content": content[:4000],
                    "truncated": len(content) > 4000,
                    "original_characters": len(content),
                }
            )
        return {
            "author_id": id,
            "revision": author["revision"],
            "title": author["title"],
            "voice": author["voice"],
            "biography": author["biography"],
            "samples": samples,
            "kind": "configured_voice_brief",
        }

    def revisions(self, id):
        with self.connection() as db:
            self._author(db, id)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT record FROM author_revisions WHERE id=? ORDER BY revision DESC",
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
                for r in db.execute("SELECT record FROM authors ORDER BY id")
            ]
        rows = [r for r in rows if q.casefold() in r["title"].casefold()]
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        }
