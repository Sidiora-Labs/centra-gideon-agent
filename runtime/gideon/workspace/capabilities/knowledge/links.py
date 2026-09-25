"""Ordered link buckets and durable repository studies over canonical Knowledge."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from .capture import CaptureError, CaptureInbox, request_key
from .repository_intake import RepositoryReader, repository_url, study
from .reviews import packed


def now():
    return datetime.now(timezone.utc).isoformat()


def link_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise CaptureError("A public HTTP or HTTPS link is required")
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.username
        or parsed.password
        or not parsed.hostname
        or parsed.port not in (None, 80, 443)
    ):
        raise CaptureError("A credential-free HTTP or HTTPS link is required")
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )


class LinkStudies:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.home = Path(home).resolve() if home else CaptureInbox._runtime_home()
        self.root = self.home / "capabilities" / "knowledge" / "repositories"
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS capability_knowledge_link_order(bucket_id TEXT NOT NULL,item_id TEXT NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(bucket_id,item_id),UNIQUE(bucket_id,position));
        CREATE TABLE IF NOT EXISTS capability_knowledge_repo_jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,bookmark_id TEXT NOT NULL,url TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,revision TEXT NOT NULL DEFAULT '',checkout_path TEXT NOT NULL DEFAULT '',report_id TEXT,error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS capability_knowledge_repo_events(job_id TEXT NOT NULL,sequence INTEGER NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL,happened_at TEXT NOT NULL,PRIMARY KEY(job_id,sequence));
        CREATE UNIQUE INDEX IF NOT EXISTS repository_bookmark_guid ON items(guid) WHERE substr(guid,1,5)='repo:';
        CREATE UNIQUE INDEX IF NOT EXISTS repository_report_guid ON items(guid) WHERE substr(guid,1,12)='repo-report:';
        """)
        self.db.execute(
            "UPDATE capability_knowledge_repo_jobs SET status='interrupted',error='Runtime stopped before repository intake finished',updated_at=? WHERE status IN ('queued','fetching','scanning')",
            (now(),),
        )
        self.db.commit()

    def assert_scope(self):
        if CaptureInbox._runtime_home() != self.home:
            raise CaptureError(
                "Runtime home changed; restore the bound allocation", 409
            )

    def _bucket(self, identity):
        bucket = self.store.get_collection(identity)
        if (
            not bucket
            or bucket.get("kind") != "manual"
            or not str(bucket.get("query", "")).startswith("link-bucket:")
        ):
            raise CaptureError("Link bucket not found", 404)
        return bucket

    def buckets(self):
        return [
            {**row, "links": self.links(row["id"])}
            for row in self.store.list_collections()
            if row.get("kind") == "manual"
            and str(row.get("query", "")).startswith("link-bucket:")
        ]

    def create_bucket(self, body):
        if (
            not isinstance(body, dict)
            or set(body) != {"name", "icon"}
            or not isinstance(body["name"], str)
            or not body["name"].strip()
            or len(body["name"]) > 100
            or not isinstance(body["icon"], str)
            or len(body["icon"]) > 40
        ):
            raise CaptureError("Bucket requires name and icon")
        self.assert_scope()
        identity = self.store.create_collection(
            name=body["name"].strip(),
            kind="manual",
            query="link-bucket:" + str(uuid4()),
            icon=body["icon"],
        )
        return {**self._bucket(identity), "links": []}

    def update_bucket(self, identity, body):
        if not isinstance(body, dict) or not body or set(body) - {"name", "icon"}:
            raise CaptureError("Bucket update accepts name and icon")
        self._bucket(identity)
        self.assert_scope()
        self.store.update_collection(identity, **body)
        return {**self._bucket(identity), "links": self.links(identity)}

    def delete_bucket(self, identity):
        self._bucket(identity)
        self.assert_scope()
        item_ids = [row["id"] for row in self.links(identity)]
        self.store.delete_collection(identity)
        self.db.execute(
            "DELETE FROM capability_knowledge_link_order WHERE bucket_id=?", (identity,)
        )
        self.db.commit()
        return {"deleted": identity, "preserved_item_ids": item_ids}

    def reorder_buckets(self, ids):
        current = [row["id"] for row in self.buckets()]
        if (
            not isinstance(ids, list)
            or len(ids) != len(set(ids))
            or set(ids) != set(current)
        ):
            raise CaptureError(
                "Bucket reorder must contain every current bucket exactly once", 409
            )
        self.assert_scope()
        with self.db:
            for position, identity in enumerate(ids):
                self.db.execute(
                    "UPDATE collections SET position=?,updated_at=? WHERE id=?",
                    (position, now(), identity),
                )
        return self.buckets()

    def links(self, bucket_id):
        self._bucket(bucket_id)
        rows = self.db.execute(
            "SELECT i.* FROM capability_knowledge_link_order o JOIN items i ON i.id=o.item_id WHERE o.bucket_id=? AND COALESCE(i.is_archived,0)=0 ORDER BY o.position",
            (bucket_id,),
        ).fetchall()
        return self.store._serialize_items(rows)

    def add_link(self, body):
        if not isinstance(body, dict) or set(body) != {"url", "title", "bucket_id"}:
            raise CaptureError("Link requires url, title and bucket_id")
        bucket = self._bucket(body["bucket_id"])
        self.assert_scope()
        url = link_url(body["url"])
        if (
            not isinstance(body["title"], str)
            or not body["title"].strip()
            or len(body["title"]) > 300
        ):
            raise CaptureError("Link title is required")
        guid = "link:" + hashlib.sha256(url.encode()).hexdigest()
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        item_id = (
            row[0]
            if row
            else self.store.create_typed_item(
                item_type="bookmark",
                title=body["title"].strip(),
                content="",
                guid=guid,
                extra={"url": url, "file_metadata": {"link_source": "manual"}},
            )
        )
        self.store.add_to_collection(bucket["id"], item_id)
        if not self.db.execute(
            "SELECT 1 FROM capability_knowledge_link_order WHERE bucket_id=? AND item_id=?",
            (bucket["id"], item_id),
        ).fetchone():
            position = self.db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM capability_knowledge_link_order WHERE bucket_id=?",
                (bucket["id"],),
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO capability_knowledge_link_order VALUES (?,?,?)",
                (bucket["id"], item_id, position),
            )
            self.db.commit()
        return self.store.get_item(item_id)

    def reorder_links(self, bucket_id, ids):
        current = [row["id"] for row in self.links(bucket_id)]
        if (
            not isinstance(ids, list)
            or len(ids) != len(set(ids))
            or set(ids) != set(current)
        ):
            raise CaptureError(
                "Link reorder must contain every current bucket link exactly once", 409
            )
        self.assert_scope()
        with self.db:
            self.db.execute(
                "UPDATE capability_knowledge_link_order SET position=position-1000000 WHERE bucket_id=?",
                (bucket_id,),
            )
            for position, identity in enumerate(ids):
                self.db.execute(
                    "UPDATE capability_knowledge_link_order SET position=? WHERE bucket_id=? AND item_id=?",
                    (position, bucket_id, identity),
                )
        return self.links(bucket_id)

    def _event(self, identity, stage, status, detail=""):
        sequence = self.db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM capability_knowledge_repo_events WHERE job_id=?",
            (identity,),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO capability_knowledge_repo_events VALUES (?,?,?,?,?,?)",
            (identity, sequence, stage, status, detail, now()),
        )
        self.db.execute(
            "UPDATE capability_knowledge_repo_jobs SET stage=?,status=?,error=?,updated_at=? WHERE id=?",
            (
                stage,
                status,
                detail if status in ("failed", "interrupted") else "",
                now(),
                identity,
            ),
        )
        self.db.commit()

    def repo(self, identity):
        row = self.db.execute(
            "SELECT * FROM capability_knowledge_repo_jobs WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise CaptureError("Repository intake not found", 404)
        result = dict(row)
        result.pop("payload")
        result["events"] = [
            dict(event)
            for event in self.db.execute(
                "SELECT sequence,stage,status,detail,happened_at FROM capability_knowledge_repo_events WHERE job_id=? ORDER BY sequence",
                (identity,),
            )
        ]
        return result

    def repos(self):
        return {
            "items": [
                self.repo(row[0])
                for row in self.db.execute(
                    "SELECT id FROM capability_knowledge_repo_jobs ORDER BY created_at DESC LIMIT 100"
                )
            ]
        }

    async def intake(self, body, session_key):
        if not isinstance(body, dict) or set(body) != {"request_id", "url"}:
            raise CaptureError("Repository intake requires request_id and url")
        request, payload = request_key(body["request_id"]), packed(body)
        prior = self.db.execute(
            "SELECT id,payload FROM capability_knowledge_repo_jobs WHERE request_id=?",
            (request,),
        ).fetchone()
        if prior:
            if prior["payload"] != payload:
                raise CaptureError(
                    "request_id belongs to different repository input", 409
                )
            return self.repo(prior["id"])
        self.assert_scope()
        host, slug, url = repository_url(body["url"])
        guid = "repo:" + host + ":" + slug.lower()
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        bookmark_id = (
            row[0]
            if row
            else self.store.create_typed_item(
                item_type="bookmark",
                title=slug,
                content="",
                guid=guid,
                extra={"url": url, "file_metadata": {"repository": True}},
            )
        )
        identity, timestamp = str(uuid4()), now()
        self.db.execute(
            "INSERT INTO capability_knowledge_repo_jobs(id,request_id,payload,bookmark_id,url,status,stage,created_at,updated_at) VALUES (?,?,?,?,?,'queued','queued',?,?)",
            (identity, request, payload, bookmark_id, url, timestamp, timestamp),
        )
        self.db.commit()
        self._event(identity, "queued", "queued", "Repository intake accepted")
        try:
            self._event(
                identity, "fetching", "fetching", "Fetching guarded repository snapshot"
            )
            destination = self.root / hashlib.sha256(url.encode()).hexdigest()[:24]
            destination.parent.mkdir(parents=True, exist_ok=True)
            fetched = await RepositoryReader(session_key).snapshot(url, destination)
            self.db.execute(
                "UPDATE capability_knowledge_repo_jobs SET revision=?,checkout_path=?,updated_at=? WHERE id=?",
                (fetched["revision"], str(destination), now(), identity),
            )
            self.db.commit()
            self._event(
                identity, "scanning", "scanning", "Studying landed repository files"
            )
            self._save_report(identity)
            self._event(
                identity, "complete", "completed", "Repository snapshot and study saved"
            )
        except Exception as exc:
            self._event(identity, self.repo(identity)["stage"], "failed", str(exc))
        return self.repo(identity)

    def _save_report(self, identity):
        job = self.repo(identity)
        structured, markdown = study(job["checkout_path"], job["revision"], job["url"])
        guid = "repo-report:" + hashlib.sha256(job["url"].encode()).hexdigest()
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        report_id = (
            row[0]
            if row
            else self.store.create_typed_item(
                item_type="note",
                title="Repository study · " + job["url"].rsplit("/", 1)[-1],
                content=markdown,
                guid=guid,
                extra={
                    "url": job["url"],
                    "file_metadata": {"repository_study": structured},
                },
            )
        )
        if row:
            self.store.update_item(
                report_id,
                content=markdown,
                file_metadata={"repository_study": structured},
            )
        self.db.execute(
            "UPDATE capability_knowledge_repo_jobs SET report_id=?,updated_at=? WHERE id=?",
            (report_id, now(), identity),
        )
        self.db.commit()
        return {
            "report_id": report_id,
            "content": markdown,
            "study": structured,
            "source_link": "#/knowledge/item/" + report_id,
        }

    def restudy(self, identity):
        self.assert_scope()
        job = self.repo(identity)
        if not job["checkout_path"] or not job["revision"]:
            raise CaptureError("Repository has no landed checkout", 409)
        self._event(
            identity, "scanning", "scanning", "Rescanning landed repository files"
        )
        result = self._save_report(identity)
        self._event(identity, "complete", "completed", "Repository study refreshed")
        return {**self.repo(identity), "report": result}

    def report(self, identity):
        job = self.repo(identity)
        if not job["report_id"]:
            raise CaptureError("Repository study report is unavailable", 404)
        item = self.store.get_item(job["report_id"])
        return {
            "report_id": item["id"],
            "content": item["content"],
            "study": item["file_metadata"]["repository_study"],
            "source_link": "#/knowledge/item/" + item["id"],
        }
