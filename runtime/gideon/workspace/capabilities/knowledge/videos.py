"""Durable reviewed and remotely acquired public-video knowledge sources."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

from gideon.core.atomic_write import atomic_write_bytes

from .capture import CaptureError, CaptureInbox, request_key
from .reviews import packed
from .transcript_format import preview, video_url
from .video_fetch import VideoCancelled, VideoReader, available


def _now():
    return datetime.now(timezone.utc).isoformat()


class VideoIngests:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.home = (
            Path(home).resolve() if home is not None else CaptureInbox._runtime_home()
        )
        self.root = self.home / "capabilities" / "knowledge" / "videos"
        self.cancellations = {}
        self.tasks = {}
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_video_jobs (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                video_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                source_id TEXT, transcript_id TEXT, original_source_id TEXT,
                artifacts TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capability_knowledge_video_events (
                job_id TEXT NOT NULL, sequence INTEGER NOT NULL, stage TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT NOT NULL, happened_at TEXT NOT NULL,
                PRIMARY KEY(job_id, sequence)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS knowledge_video_source_guid ON items(guid)
                WHERE substr(guid,1,13)='video:source:';
            CREATE UNIQUE INDEX IF NOT EXISTS knowledge_video_transcript_guid ON items(guid)
                WHERE substr(guid,1,17)='video:transcript:';
            CREATE UNIQUE INDEX IF NOT EXISTS knowledge_video_original_guid ON items(guid)
                WHERE substr(guid,1,15)='video:original:';
        """)
        now = _now()
        self.db.execute(
            "UPDATE capability_knowledge_video_jobs SET status='interrupted',error='Runtime stopped before this job finished; start a new request to retry.',updated_at=? WHERE status IN ('pending','running','cancelling')",
            (now,),
        )
        self.db.commit()

    def assert_scope(self):
        if CaptureInbox._runtime_home() != self.home:
            raise CaptureError(
                "Runtime home changed; restore the bound allocation before ingesting video",
                409,
            )

    def _event(self, identity, stage, status, detail=""):
        sequence = self.db.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM capability_knowledge_video_events WHERE job_id=?",
            (identity,),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO capability_knowledge_video_events VALUES (?,?,?,?,?,?)",
            (identity, sequence, stage, status, detail, _now()),
        )
        self.db.execute(
            "UPDATE capability_knowledge_video_jobs SET stage=?,status=?,error=?,updated_at=? WHERE id=?",
            (
                stage,
                status,
                detail if status in ("failed", "interrupted") else "",
                _now(),
                identity,
            ),
        )
        self.db.commit()

    def _row(self, identity):
        row = self.db.execute(
            "SELECT * FROM capability_knowledge_video_jobs WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise CaptureError("Video ingest job not found", 404)
        result = dict(row)
        result["artifacts"] = json.loads(result["artifacts"])
        result["events"] = [
            dict(event)
            for event in self.db.execute(
                "SELECT sequence,stage,status,detail,happened_at FROM capability_knowledge_video_events WHERE job_id=? ORDER BY sequence",
                (identity,),
            )
        ]
        result.pop("payload")
        return result

    def get(self, identity):
        return self._row(identity)

    def list(self, limit=20, offset=0):
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 100
            or not 0 <= offset <= 1000000
        ):
            raise CaptureError("limit must be 1..100 and offset 0..1000000")
        total = self.db.execute(
            "SELECT count(*) FROM capability_knowledge_video_jobs"
        ).fetchone()[0]
        rows = self.db.execute(
            "SELECT id FROM capability_knowledge_video_jobs ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        reason = (
            ""
            if available()
            else "Install the optional yt-dlp dependency to retrieve public captions; supplied transcripts remain available."
        )
        return {
            "items": [self.get(row[0]) for row in rows],
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
            "availability": {"caption_retrieval": available(), "reason": reason},
        }

    def _start(self, request_id, payload, title):
        key = request_key(request_id)
        encoded = packed(payload)
        previous = self.db.execute(
            "SELECT id,payload FROM capability_knowledge_video_jobs WHERE request_id=?",
            (key,),
        ).fetchone()
        if previous:
            if previous["payload"] != encoded:
                raise CaptureError(
                    "request_id already belongs to a different video ingest", 409
                )
            return previous["id"], False
        self.assert_scope()
        video_id, url = video_url(payload["url"])
        identity, now = str(uuid4()), _now()
        self.db.execute(
            "INSERT INTO capability_knowledge_video_jobs (id,request_id,payload,video_id,url,title,status,stage,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                key,
                encoded,
                video_id,
                url,
                title,
                "pending",
                "queued",
                now,
                now,
            ),
        )
        self.db.commit()
        self._event(identity, "queued", "pending", "Request accepted")
        return identity, True

    def import_preview(self, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "url",
            "title",
            "format",
            "content",
            "language",
            "preview_id",
        }:
            raise CaptureError(
                "Reviewed transcript import requires request_id, url, title, format, content, language and preview_id"
            )
        reviewed = preview(
            {
                key: body[key]
                for key in ("url", "title", "format", "content", "language")
            }
        )
        if body["preview_id"] != reviewed["preview_id"]:
            raise CaptureError("Transcript changed after review", 409)
        identity, created = self._start(
            body["request_id"], {**body, "kind": "supplied"}, reviewed["title"]
        )
        if created:
            self._event(
                identity,
                "transcript",
                "running",
                "Persisting reviewed supplied transcript",
            )
            try:
                self._persist(identity, reviewed, "user_supplied", body["content"])
                self._event(
                    identity, "complete", "completed", "Reviewed transcript saved"
                )
            except Exception as exc:
                self._event(identity, "transcript", "failed", str(exc))
                raise
        return self.get(identity)

    def start_fetch(self, body, session_key):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "url",
            "language",
            "transcript",
            "video",
            "audio",
        }:
            raise CaptureError(
                "Video fetch requires request_id, url, language, transcript, video and audio"
            )
        if any(
            type(body[key]) is not bool for key in ("transcript", "video", "audio")
        ) or not any(body[key] for key in ("transcript", "video", "audio")):
            raise CaptureError(
                "Choose at least one transcript, video or audio artifact"
            )
        if (
            not isinstance(body["language"], str)
            or not body["language"]
            or len(body["language"]) > 30
        ):
            raise CaptureError("A language tag of at most 30 characters is required")
        identity, created = self._start(
            body["request_id"], {**body, "kind": "fetch"}, "Public video"
        )
        if created:
            cancellation = Event()
            self.cancellations[identity] = cancellation
            self.tasks[identity] = asyncio.create_task(
                self._fetch(identity, body, session_key, cancellation)
            )
        return self.get(identity)

    async def _fetch(self, identity, body, session_key, cancellation):
        reader = VideoReader(session_key, cancellation)
        try:
            self._event(
                identity, "metadata", "running", "Reading public video metadata"
            )
            metadata = await asyncio.to_thread(reader.metadata, body["url"])
            title = str(metadata.get("title") or "Untitled public video")[:300]
            duration = float(metadata.get("duration") or 0)
            if duration < 0 or duration > 21600:
                raise CaptureError(
                    "Video duration exceeds the six-hour acquisition limit", 413
                )
            self.db.execute(
                "UPDATE capability_knowledge_video_jobs SET title=?,updated_at=? WHERE id=?",
                (title, _now(), identity),
            )
            self.db.commit()
            if body["transcript"]:
                self._event(
                    identity, "transcript", "running", "Retrieving caption track"
                )
                content, format_name, language, provenance = await reader.captions(
                    metadata, body["language"]
                )
                reviewed = preview(
                    {
                        "url": body["url"],
                        "title": title,
                        "format": format_name,
                        "content": content,
                        "language": language,
                    }
                )
                self._persist(identity, reviewed, provenance, content)
            for kind in ("audio", "video"):
                if body[kind]:
                    self._event(
                        identity,
                        kind,
                        "running",
                        "Retrieving bounded " + kind + " artifact",
                    )
                    data, extension = await reader.media(metadata, kind)
                    self._save_artifact(identity, kind, data, extension)
            self._event(identity, "complete", "completed", "Requested artifacts saved")
        except VideoCancelled:
            self._event(identity, "cancelled", "cancelled", "Cancellation completed")
        except Exception as exc:
            self._event(identity, self.get(identity)["stage"], "failed", str(exc))
        finally:
            self.cancellations.pop(identity, None)
            self.tasks.pop(identity, None)

    def cancel(self, identity):
        current = self.get(identity)
        cancellation = self.cancellations.get(identity)
        if cancellation is None or current["status"] not in (
            "pending",
            "running",
            "cancelling",
        ):
            raise CaptureError("Video ingest is not running", 409)
        cancellation.set()
        self._event(identity, current["stage"], "cancelling", "Cancellation requested")
        return self.get(identity)

    def _upsert_item(self, guid, item_type, title, content="", **extra):
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        if row:
            identity = row[0]
            self.store.update_item(identity, title=title, content=content, **extra)
        else:
            identity = self.store.create_typed_item(
                item_type=item_type,
                title=title,
                content=content,
                guid=guid,
                extra=extra,
            )
        return identity

    def _persist(self, job_id, reviewed, provenance, original):
        self.assert_scope()
        video_id, title, url = reviewed["video_id"], reviewed["title"], reviewed["url"]
        original_id = self._upsert_item(
            "video:original:" + video_id,
            "note",
            title + " · original captions",
            original,
            file_metadata={
                "video_id": video_id,
                "caption_provenance": provenance,
                "language": reviewed["language"],
                "format": reviewed["format"],
            },
        )
        markdown = (
            "# "
            + title
            + "\n\nSource: "
            + url
            + "\nCaption provenance: "
            + provenance
            + "\nLanguage: "
            + reviewed["language"]
            + "\n\n"
            + "\n\n".join(
                "["
                + self._clock(row["start"])
                + "]("
                + row["source_link"]
                + ") "
                + row["text"]
                for row in reviewed["segments"]
            )
        )
        source_id = self._upsert_item(
            "video:source:" + video_id,
            "bookmark",
            title,
            url=url,
            file_metadata={"video_id": video_id, "ingest_job_id": job_id},
        )
        transcript_id = self._upsert_item(
            "video:transcript:" + video_id,
            "note",
            title + " · transcript",
            markdown,
            url=url,
            file_metadata={
                "video_id": video_id,
                "caption_provenance": provenance,
                "language": reviewed["language"],
                "segments": reviewed["segments"],
                "original_source_id": original_id,
            },
        )
        self.db.execute(
            "UPDATE capability_knowledge_video_jobs SET source_id=?,transcript_id=?,original_source_id=?,title=?,updated_at=? WHERE id=?",
            (source_id, transcript_id, original_id, title, _now(), job_id),
        )
        self.db.commit()

    @staticmethod
    def _clock(seconds):
        value = int(seconds)
        return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"

    def _save_artifact(self, job_id, kind, data, extension):
        self.assert_scope()
        if not data:
            raise CaptureError("Downloaded artifact is empty", 502)
        job = self.get(job_id)
        folder = self.root / job["video_id"]
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        path = folder / f"{kind}-{digest}.{extension}"
        if not path.exists():
            atomic_write_bytes(path, data, fsync=True)
        artifacts = job["artifacts"] + [
            {"kind": kind, "path": str(path), "bytes": len(data), "sha256": digest}
        ]
        self.db.execute(
            "UPDATE capability_knowledge_video_jobs SET artifacts=?,updated_at=? WHERE id=?",
            (packed(artifacts), _now(), job_id),
        )
        self.db.commit()

    def transcript(self, identity):
        job = self.get(identity)
        item = (
            self.store.get_item(job["transcript_id"]) if job["transcript_id"] else None
        )
        if item is None:
            raise CaptureError("No transcript is stored for this video ingest", 404)
        return {
            "content": item["content"],
            "transcript_id": item["id"],
            "source_link": "#/knowledge/item/" + item["id"],
        }
