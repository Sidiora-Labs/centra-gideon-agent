"""Writing works and exercises with canonical immutable manuscript artifacts."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .authors import AuthorStore
from .universes import UniverseStore
from .store import CatalogError, IngredientStore, identifier, integer, keys, text

FIELDS = {"title", "kind", "prompt", "author_ref", "universe_ref", "active_draft_id"}


class WorkStore(IngredientStore):
    def __init__(self, home=None):
        super().__init__(home)
        self.authors = AuthorStore(self.home)
        self.universes = UniverseStore(self.home)
        self.artifacts = NativeArtifactProvider(self.home / "artifacts")
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS work_revisions(id TEXT, revision INTEGER, record TEXT,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS work_requests(id TEXT PRIMARY KEY, payload TEXT, record TEXT);
                CREATE TABLE IF NOT EXISTS work_draft_requests(id TEXT PRIMARY KEY, payload_hash TEXT, record TEXT);
                CREATE TABLE IF NOT EXISTS work_drafts(id TEXT PRIMARY KEY, work_id TEXT, record TEXT);
            """)

    def _work(self, db, id):
        row = db.execute("SELECT record FROM works WHERE id=?", (identifier(id),)).fetchone()
        if not row:
            raise CatalogError("Work not found", 404)
        return json.loads(row[0])

    def _values(self, db, value, history=()):
        keys(value, FIELDS)
        kind = value.get("kind", "work")
        if kind not in ("work", "exercise"):
            raise CatalogError("Invalid work kind")
        result = {"title": text(value.get("title"), 200, True), "kind": kind, "prompt": text(value.get("prompt", ""), 20000)}
        for field, table in (("author_ref", "author_revisions"), ("universe_ref", "universe_revisions")):
            ref = value.get(field)
            if ref is not None:
                keys(ref, {"id", "revision"})
                pair = (identifier(ref.get("id")), integer(ref.get("revision")))
                old = any(row.get(field) == ref for row in history)
                if not old and not db.execute(f"SELECT 1 FROM {table} WHERE id=? AND revision=?", pair).fetchone():
                    raise CatalogError("Pinned context revision not found", 404)
                ref = {"id": pair[0], "revision": pair[1]}
            result[field] = ref
        active = value.get("active_draft_id")
        if active is not None:
            identifier(active)
            if not any(row.get("active_draft_id") == active for row in history):
                raise CatalogError("Select a draft through the draft operation")
        result["active_draft_id"] = active
        return result

    def _save(self, db, record):
        encoded = json.dumps(record, sort_keys=True)
        db.execute("INSERT INTO work_revisions VALUES(?,?,?)", (record["id"], record["revision"], encoded))
        db.execute("INSERT OR REPLACE INTO works VALUES(?,?)", (record["id"], encoded))
        return record

    def create(self, payload):
        keys(payload, FIELDS | {"request_id"})
        request = identifier(payload.get("request_id"))
        body = {k: v for k, v in payload.items() if k in FIELDS}
        encoded = json.dumps(body, sort_keys=True)
        with self.connection() as db:
            prior = db.execute("SELECT payload,record FROM work_requests WHERE id=?", (request,)).fetchone()
            if prior:
                if prior[0] != encoded:
                    raise CatalogError("Request ID already used with different values", 409)
                return json.loads(prior[1])
            values = self._values(db, body)
            now = datetime.now(timezone.utc).isoformat()
            record = {**values, "id": str(uuid4()), "revision": 1, "created_at": now, "updated_at": now}
            self._save(db, record)
            db.execute("INSERT INTO work_requests VALUES(?,?,?)", (request, encoded, json.dumps(record)))
            return record

    def update(self, id, patch):
        keys(patch, FIELDS | {"revision"})
        revision = integer(patch.get("revision"))
        with self.connection() as db:
            old = self._work(db, id)
            if old["revision"] != revision:
                raise CatalogError("Work changed; reload before saving", 409)
            history = [json.loads(r[0]) for r in db.execute("SELECT record FROM work_revisions WHERE id=?", (id,))]
            values = self._values(db, {k: patch.get(k, self.editable(old)[k]) for k in FIELDS}, history)
            return self._save(db, {**old, **values, "revision": revision + 1, "updated_at": datetime.now(timezone.utc).isoformat()})

    @staticmethod
    def editable(record):
        return {key: record[key] for key in FIELDS}

    def restore(self, id, payload):
        keys(payload, {"revision", "target_revision"})
        target = integer(payload.get("target_revision"))
        record = self.export(id, target)
        return self.update(id, {**self.editable(record), "revision": payload.get("revision")})

    def export(self, id, revision=None):
        with self.connection() as db:
            current = self._work(db, id)
            if revision is None:
                return current
            integer(revision)
            row = db.execute("SELECT record FROM work_revisions WHERE id=? AND revision=?", (id, revision)).fetchone()
            if not row:
                raise CatalogError("Revision not found", 404)
            return json.loads(row[0])

    def drafts(self, id):
        with self.connection() as db:
            self._work(db, id)
            return {"items": [json.loads(row[0]) for row in db.execute("SELECT record FROM work_drafts WHERE work_id=? ORDER BY rowid DESC", (id,))]}

    def draft(self, id, payload):
        with self.connection() as db:
            return self.draft_in_transaction(db, id, payload)

    def draft_in_transaction(self, db, id, payload):
        keys(payload, {"request_id", "revision", "text", "note"})
        request = identifier(payload.get("request_id"))
        content = text(payload.get("text"), 1000000)
        if not content.strip():
            raise CatalogError("Draft text is required")
        note = text(payload.get("note", ""), 2000)
        digest = hashlib.sha256(json.dumps({"id": identifier(id), **payload}, sort_keys=True).encode()).hexdigest()
        prior = db.execute("SELECT payload_hash,record FROM work_draft_requests WHERE id=?", (request,)).fetchone()
        if prior:
            if prior[0] != digest:
                raise CatalogError("Draft request already used with different values", 409)
            return json.loads(prior[1])
        work = self._work(db, id)
        if integer(payload.get("revision")) != work["revision"]:
            raise CatalogError("Work changed; reload before saving a draft", 409)
        draft_id = hashlib.sha256((id + ":" + request).encode()).hexdigest()
        slug = "creative-draft-" + draft_id
        artifact = self.artifacts.get(slug, version=1)
        if artifact:
            if artifact.content != content or not artifact.readonly or artifact.description != digest:
                raise CatalogError("Draft artifact conflicts with this request", 409)
        else:
            artifact = self.artifacts.create(name=work["title"] + " draft", slug=slug, kind="markdown", content=content,
                                             description=digest, readonly=True)
            if artifact.slug != slug:
                raise CatalogError("Draft artifact name changed; retry", 409)
        persisted = self.artifacts.get(slug, version=1)
        if persisted is None or persisted.content != content or not persisted.readonly or persisted.description != digest:
            raise CatalogError("Draft artifact persistence could not be confirmed", 500)
        draft = {"id": draft_id, "artifact_id": artifact.slug, "artifact_version": 1, "note": note,
                 "created_at": datetime.now(timezone.utc).isoformat(), "characters": len(content)}
        db.execute("INSERT INTO work_drafts VALUES(?,?,?)", (draft_id, id, json.dumps(draft)))
        record = self._save(db, {**work, "active_draft_id": draft_id, "revision": work["revision"] + 1, "updated_at": draft["created_at"]})
        result = {"work": record, "draft": draft}
        db.execute("INSERT INTO work_draft_requests VALUES(?,?,?)", (request, digest, json.dumps(result)))
        return result

    def read_draft(self, id, draft_id):
        with self.connection() as db:
            self._work(db, id)
            row = db.execute("SELECT record FROM work_drafts WHERE id=? AND work_id=?", (identifier(draft_id), id)).fetchone()
            if not row:
                raise CatalogError("Draft not found", 404)
            draft = json.loads(row[0])
        artifact = self.artifacts.get(draft["artifact_id"], version=draft["artifact_version"])
        return {**draft, "missing": artifact is None, "text": artifact.content if artifact else ""}

    def get(self, id):
        work = self.export(id)
        with self.connection() as db:
            row = db.execute("SELECT record FROM work_drafts WHERE id=? AND work_id=?", (work["active_draft_id"], id)).fetchone()
        active = json.loads(row[0]) if row else None
        artifact = self.artifacts.get(active["artifact_id"], version=active["artifact_version"]) if active else None
        return {**work, "active_draft": active, "draft_missing": active is not None and artifact is None,
                "text": artifact.content if artifact else ""}

    def context(self, id):
        work = self.export(id)
        result = {"prompt": work["prompt"], "author": None, "universe": None, "missing": []}
        for field, store, key in (("author_ref", self.authors, "author"), ("universe_ref", self.universes, "universe")):
            ref = work[field]
            if ref:
                try:
                    result[key] = store.brief(ref["id"], ref["revision"]) if key == "author" else store.export(ref["id"], ref["revision"])
                except CatalogError as exc:
                    if exc.status != 404:
                        raise
                    result["missing"].append(field)
        return result

    def revisions(self, id):
        with self.connection() as db:
            self._work(db, id)
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM work_revisions WHERE id=? ORDER BY revision DESC", (id,))]

    def list(self, q="", offset=0, limit=25):
        text(q, 200)
        integer(offset, 0, 1000000)
        integer(limit, 1, 100)
        with self.connection() as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT record FROM works ORDER BY id")]
        rows = [r for r in rows if q.casefold() in r["title"].casefold()]
        return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}

