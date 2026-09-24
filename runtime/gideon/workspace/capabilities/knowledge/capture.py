"""Chronological capture receipts in the existing knowledge database."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class CaptureError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
        raise CaptureError("request_id must contain 8..128 letters, digits, underscores or hyphens")
    return value


def text_field(value, name, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise CaptureError(f"{name} must be nonempty text of at most {maximum} characters")
    return value


class CaptureInbox:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        database = self.db.execute("PRAGMA database_list").fetchone()[2]
        self.files_root = Path(database).resolve().parent / "files"
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_captures (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                input_origin TEXT NOT NULL, original_text TEXT NOT NULL,
                audio_item_id TEXT, audio_sha256 TEXT, captured_at TEXT NOT NULL,
                status TEXT NOT NULL, transcript TEXT, error TEXT,
                revision INTEGER NOT NULL DEFAULT 1, destination_id TEXT, pending TEXT
            );
            CREATE TABLE IF NOT EXISTS capability_knowledge_capture_events (
                capture_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
                event TEXT NOT NULL, payload TEXT NOT NULL, happened_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS capture_original_immutable
            BEFORE UPDATE OF id,request_id,input_origin,original_text,audio_item_id,audio_sha256,captured_at
            ON capability_knowledge_captures BEGIN SELECT RAISE(ABORT,'capture provenance is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS capture_event_immutable_update
            BEFORE UPDATE ON capability_knowledge_capture_events BEGIN SELECT RAISE(ABORT,'capture history is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS capture_event_immutable_delete
            BEFORE DELETE ON capability_knowledge_capture_events BEGIN SELECT RAISE(ABORT,'capture history is immutable'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS capture_destination_guid ON items(guid)
                WHERE substr(guid,1,8) = 'capture:';
        """)

    def get(self, identity):
        row = self.db.execute("SELECT * FROM capability_knowledge_captures WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise CaptureError("Capture not found", 404)
        result = dict(row)
        result["text"] = result.pop("original_text")
        result.pop("pending")
        result["events"] = [dict(event) | {"payload": json.loads(event["payload"])} for event in self.db.execute(
            "SELECT event,payload,happened_at FROM capability_knowledge_capture_events WHERE capture_id=? ORDER BY rowid", (identity,)
        )]
        result["source_link"] = f"#/knowledge/item/{result['destination_id']}" if result["destination_id"] else None
        return result

    def list(self, limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise CaptureError("limit must be 1..100 and offset 0..1000000")
        total = self.db.execute("SELECT count(*) FROM capability_knowledge_captures").fetchone()[0]
        rows = self.db.execute("SELECT id FROM capability_knowledge_captures ORDER BY captured_at DESC,id LIMIT ? OFFSET ?", (limit, offset))
        return {"items": [self.get(row["id"]) for row in rows], "total": total, "limit": limit, "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None}

    def create(self, request_id, text="", *, audio_item_id=None, audio_sha256=None):
        request_id = request_key(request_id)
        text_field(text, "text", 100000, required=audio_item_id is None)
        origin = "voice" if audio_item_id else "text"
        previous = self.db.execute("SELECT * FROM capability_knowledge_captures WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if (previous["original_text"], previous["input_origin"], previous["audio_sha256"]) != (text, origin, audio_sha256):
                raise CaptureError("request_id already belongs to different input", 409)
            return self.get(previous["id"])
        if audio_item_id:
            source = self.store.get_item(audio_item_id)
            if not source or source.get("item_type", source.get("type")) != "audio":
                raise CaptureError("Audio source must exist in this knowledge store", 404)
        identity, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        self.db.execute("INSERT INTO capability_knowledge_captures (id,request_id,input_origin,original_text,audio_item_id,audio_sha256,captured_at,status) VALUES (?,?,?,?,?,?,?,?)",
                        (identity, request_id, origin, text, audio_item_id, audio_sha256, now, "needs_review"))
        self.db.commit()
        return self.get(identity)

    def save_audio(self, request_id, data, filename, mime):
        request_key(request_id)
        suffix = Path(filename).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac"} or not mime.startswith("audio/"):
            raise CaptureError("Supported audio file required", 415)
        if not data or len(data) > 20 * 1024 * 1024:
            raise CaptureError("Audio must contain 1 byte to 20 MiB", 413)
        digest = hashlib.sha256(data).hexdigest()
        existing = self.db.execute("SELECT id FROM capability_knowledge_captures WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            record = self.get(existing["id"])
            if record["input_origin"] != "voice" or record["audio_sha256"] != digest:
                raise CaptureError("request_id already belongs to different input", 409)
            return record
        self.files_root.mkdir(parents=True, exist_ok=True)
        path = self.files_root / f"capture-{digest}{suffix}"
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise CaptureError("Stored audio integrity mismatch", 409)
        if not path.exists():
            from gideon.core.atomic_write import atomic_write_bytes
            atomic_write_bytes(path, data, fsync=True)
        guid = f"capture:audio:{request_id}"
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        audio_id = row["id"] if row else self.store.create_typed_item(item_type="audio", title=Path(filename).name, guid=guid,
            extra={"file_path": str(path), "file_size": len(data), "mime_type": mime,
                   "file_metadata": {"content_hash": digest, "original_filename": Path(filename).name}})
        return self.create(request_id, audio_item_id=audio_id, audio_sha256=digest)

    async def transcribe(self, identity):
        from gideon.integrations.transcribe import is_available, transcribe_audio
        record = self.get(identity)
        if record["input_origin"] != "voice":
            raise CaptureError("Only voice captures can be transcribed")
        if record["transcript"]:
            return record
        item = self.store.get_item(record["audio_item_id"])
        path = Path(item.get("file_path", "")) if item else Path("")
        if not path.is_file() or not path.resolve().is_relative_to(self.files_root) or hashlib.sha256(path.read_bytes()).hexdigest() != record["audio_sha256"]:
            raise CaptureError("Original audio is missing or changed", 409)
        status, transcript, error = "transcription_unavailable", None, "Speech transcription is not available; original audio is preserved."
        if await is_available():
            try:
                transcript = await transcribe_audio(str(path))
                transcript = transcript if transcript and transcript.strip() else None
                status = "needs_review" if transcript and transcript.strip() else "transcription_failed"
                error = None if status == "needs_review" else "No transcript was returned; original audio is preserved."
            except Exception:
                status, error = "transcription_failed", "Transcription failed; retry from the preserved audio."
        status = "routed" if record["destination_id"] else status
        self.db.execute("UPDATE capability_knowledge_captures SET status=?,transcript=?,error=?,revision=revision+1 WHERE id=? AND transcript IS NULL", (status, transcript, error, identity))
        self.db.commit()
        return self.get(identity)

    def route(self, identity, payload):
        if set(payload) != {"request_id", "revision", "destination", "title", "content"}:
            raise CaptureError("Route requires request_id, revision, destination, title and content only")
        key = request_key(payload["request_id"])
        if payload["destination"] not in {"note", "journal", "fleeting"}:
            raise CaptureError("destination must be note, journal or fleeting")
        text_field(payload["title"], "title", 300)
        text_field(payload["content"], "content", 100000)
        if type(payload["revision"]) is not int:
            raise CaptureError("revision must be an integer")
        current = self.get(identity)
        packed = json.dumps(payload, sort_keys=True)
        prior = self.db.execute("SELECT * FROM capability_knowledge_capture_events WHERE request_id=?", (key,)).fetchone()
        if prior:
            if prior["capture_id"] != identity or prior["payload"] != packed:
                raise CaptureError("request_id already belongs to a different route", 409)
            if prior["event"] == "routed":
                return current
        row = self.db.execute("SELECT pending FROM capability_knowledge_captures WHERE id=?", (identity,)).fetchone()
        if row["pending"] and row["pending"] != packed:
            raise CaptureError("A previous route must be retried before another change", 409)
        if current["revision"] != payload["revision"]:
            raise CaptureError("Capture changed; reload before routing", 409)
        self.db.execute("UPDATE capability_knowledge_captures SET pending=? WHERE id=?", (packed, identity))
        self.db.commit()
        destination_id = current["destination_id"]
        if destination_id and self.store.get_item(destination_id) is None:
            raise CaptureError("Destination was removed; original capture is preserved", 409)
        if not destination_id:
            guid = f"capture:route:{identity}"
            existing = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
            destination_id = existing["id"] if existing else self.store.create_typed_item(
                item_type=payload["destination"], title=payload["title"], content=payload["content"], guid=guid,
                extra={"file_metadata": {"capture_id": identity, "original_at": current["captured_at"]}})
        item = self.store.get_item(destination_id)
        metadata = item.get("file_metadata") or {}
        self.store.update_item(destination_id, item_type=payload["destination"], title=payload["title"], content=payload["content"],
            file_metadata={**metadata, "capture_id": identity, "original_at": current["captured_at"]})
        self.db.execute("BEGIN")
        try:
            self.db.execute("INSERT INTO capability_knowledge_capture_events VALUES (?,?,?,?,?)", (identity, key, "routed", packed, datetime.now(timezone.utc).isoformat()))
            self.db.execute("UPDATE capability_knowledge_captures SET destination_id=?,status='routed',revision=revision+1,pending=NULL WHERE id=?", (destination_id, identity))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.get(identity)
