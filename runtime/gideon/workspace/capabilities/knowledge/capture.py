"""Chronological capture receipts in the existing knowledge database."""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.core.config.loader import CONFIG_DIR_NAME
from gideon.core.config.locations import configuration_home


class CaptureError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
        raise CaptureError(
            "request_id must contain 8..128 letters, digits, underscores or hyphens"
        )
    return value


def text_field(value, name, maximum, required=True):
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (required and not value.strip())
    ):
        raise CaptureError(
            f"{name} must be nonempty text of at most {maximum} characters"
        )
    return value


async def _admit_text(text, surface, request=None):
    from gideon.workspace.uploads.content_intake import approve_text

    if request is not None:
        from gideon.security.approval_answer import (
            OWNER,
            of_request,
            work_principal_of_request,
        )
        from gideon.security.session_credentials import work_of_request

        proof = work_of_request(request)
        # Only an authenticated owner request is an owner-authored edit. A tool
        # running on the owner's behalf is still externally produced text.
        if (
            of_request(request).kind == OWNER
            and work_principal_of_request(request).kind == OWNER
            and not (request.headers.get("X-Session-Proof") and proof is None)
            and (proof is None or proof.initiator.kind == OWNER)
        ):
            return text
    return (await approve_text(text, surface=surface)).text


async def _joined_transcription(function, path):
    task = asyncio.create_task(function(path))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


class CaptureInbox:
    def __init__(self, store, home=None):
        self.runtime_home = (
            Path(home).resolve() if home is not None else self._runtime_home()
        )
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

    @staticmethod
    def _runtime_home():
        return configuration_home(
            os.environ.get("GIDEON_HOME"),
            Path.home() / CONFIG_DIR_NAME,
            logging.getLogger(__name__),
        ).resolve()

    def assert_write_scope(self):
        if self._runtime_home() != self.runtime_home:
            raise CaptureError(
                "Runtime home changed; restore the bound allocation before writing personal knowledge",
                409,
            )

    def get(self, identity):
        row = self.db.execute(
            "SELECT * FROM capability_knowledge_captures WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise CaptureError("Capture not found", 404)
        result = dict(row)
        result["text"] = result.pop("original_text")
        result.pop("pending")
        result["events"] = [
            dict(event) | {"payload": json.loads(event["payload"])}
            for event in self.db.execute(
                "SELECT event,payload,happened_at FROM capability_knowledge_capture_events WHERE capture_id=? ORDER BY rowid",
                (identity,),
            )
        ]
        result["source_link"] = (
            f"#/knowledge/item/{result['destination_id']}"
            if result["destination_id"]
            else None
        )
        return result

    def list(self, limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise CaptureError("limit must be 1..100 and offset 0..1000000")
        total = self.db.execute(
            "SELECT count(*) FROM capability_knowledge_captures"
        ).fetchone()[0]
        rows = self.db.execute(
            "SELECT id FROM capability_knowledge_captures ORDER BY captured_at DESC,id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return {
            "items": [self.get(row["id"]) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    async def create(
        self,
        request_id,
        text="",
        *,
        audio_item_id=None,
        audio_sha256=None,
        _request=None,
    ):
        request_id = request_key(request_id)
        text_field(text, "text", 100000, required=audio_item_id is None)
        origin = "voice" if audio_item_id else "text"
        previous = self.db.execute(
            "SELECT * FROM capability_knowledge_captures WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if previous:
            if (
                previous["original_text"],
                previous["input_origin"],
                previous["audio_sha256"],
            ) != (text, origin, audio_sha256):
                raise CaptureError("request_id already belongs to different input", 409)
            return self.get(previous["id"])
        if text:
            text = await _admit_text(text, "capture_text", _request)
        previous = self.db.execute(
            "SELECT id,original_text,input_origin,audio_sha256 FROM capability_knowledge_captures WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if previous:
            if (
                previous["original_text"],
                previous["input_origin"],
                previous["audio_sha256"],
            ) != (text, origin, audio_sha256):
                raise CaptureError("request_id already belongs to different input", 409)
            return self.get(previous["id"])
        self.assert_write_scope()
        if audio_item_id:
            source = self.store.get_item(audio_item_id)
            if not source or source.get("item_type", source.get("type")) != "audio":
                raise CaptureError(
                    "Audio source must exist in this knowledge store", 404
                )
        identity, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO capability_knowledge_captures (id,request_id,input_origin,original_text,audio_item_id,audio_sha256,captured_at,status) VALUES (?,?,?,?,?,?,?,?)",
            (
                identity,
                request_id,
                origin,
                text,
                audio_item_id,
                audio_sha256,
                now,
                "needs_review",
            ),
        )
        self.db.commit()
        return self.get(identity)

    async def save_audio(self, request_id, data, filename, mime):
        request_key(request_id)
        if Path(filename).suffix.lower() not in {
            ".wav",
            ".mp3",
            ".m4a",
            ".webm",
            ".ogg",
            ".flac",
        } or not mime.startswith("audio/"):
            raise CaptureError("Supported audio file required", 415)
        self.assert_write_scope()
        from gideon.workspace.uploads.content_intake import ApprovedFile, approve_stream

        if isinstance(data, ApprovedFile):
            data.require_approved()
            return await self._save_audio(request_id, data, filename, mime)
        if not data or len(data) > 20 * 1024 * 1024:
            raise CaptureError("Audio must contain 1 byte to 20 MiB", 413)

        async def chunks():
            yield data

        snapshot = await approve_stream(
            chunks(), filename, mime, surface="capture_audio"
        )
        try:
            return await self._save_audio(request_id, snapshot, filename, mime)
        finally:
            snapshot.close()

    async def _save_audio(self, request_id, snapshot, filename, mime):
        request_key(request_id)
        suffix = Path(filename).suffix.lower()
        if suffix not in {
            ".wav",
            ".mp3",
            ".m4a",
            ".webm",
            ".ogg",
            ".flac",
        } or not mime.startswith("audio/"):
            raise CaptureError("Supported audio file required", 415)
        if not snapshot.size or snapshot.size > 20 * 1024 * 1024:
            raise CaptureError("Audio must contain 1 byte to 20 MiB", 413)
        digest = snapshot.digest
        existing = self.db.execute(
            "SELECT id FROM capability_knowledge_captures WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if existing:
            record = self.get(existing["id"])
            if record["input_origin"] != "voice" or record["audio_sha256"] != digest:
                raise CaptureError("request_id already belongs to different input", 409)
            return record
        self.assert_write_scope()
        self.files_root.mkdir(parents=True, exist_ok=True)
        path = self.files_root / f"capture-{digest}{suffix}"
        created = False
        try:
            await snapshot.persist(path)
            created = True
        except FileExistsError:
            from gideon.workspace.uploads.content_intake import approve_path

            existing_file = await approve_path(path, surface="capture_audio_existing")
            try:
                if existing_file.digest != digest:
                    raise CaptureError("Stored audio integrity mismatch", 409)
            finally:
                existing_file.close()
        try:
            self.assert_write_scope()
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        guid = f"capture:audio:{request_id}"
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        audio_id = (
            row["id"]
            if row
            else self.store.create_typed_item(
                item_type="audio",
                title=Path(filename).name,
                guid=guid,
                extra={
                    "file_path": str(path),
                    "file_size": snapshot.size,
                    "mime_type": mime,
                    "file_metadata": {
                        "content_hash": digest,
                        "original_filename": Path(filename).name,
                    },
                },
            )
        )
        return await self.create(
            request_id, audio_item_id=audio_id, audio_sha256=digest
        )

    async def transcribe(self, identity):
        from gideon.integrations.transcribe import is_available, transcribe_audio

        record = self.get(identity)
        if record["input_origin"] != "voice":
            raise CaptureError("Only voice captures can be transcribed")
        if record["transcript"]:
            return record
        self.assert_write_scope()
        item = self.store.get_item(record["audio_item_id"])
        path = Path(item.get("file_path", "")) if item else Path("")
        if not path.resolve().is_relative_to(self.files_root):
            raise CaptureError("Original audio is missing or changed", 409)
        from gideon.workspace.uploads.content_intake import IntakeRefused, approve_path

        try:
            snapshot = await approve_path(path, surface="capture_transcribe_audio")
        except OSError as exc:
            raise CaptureError("Original audio is missing or changed", 409) from exc
        refusal = None
        error: str | None
        try:
            if snapshot.digest != record["audio_sha256"]:
                raise CaptureError("Original audio is missing or changed", 409)
            status, transcript, error = (
                "transcription_unavailable",
                None,
                "Speech transcription is not available; original audio is preserved.",
            )
            if await is_available():
                try:
                    async with snapshot.reader_path() as owned_path:
                        transcript = await _joined_transcription(
                            transcribe_audio, owned_path
                        )
                    transcript = (
                        transcript if transcript and transcript.strip() else None
                    )
                    if transcript:
                        transcript = await _admit_text(transcript, "capture_transcript")
                    status = "needs_review" if transcript else "transcription_failed"
                    error = (
                        None
                        if transcript
                        else "No transcript was returned; original audio is preserved."
                    )
                except IntakeRefused as exc:
                    refusal = exc
                    status, transcript, error = (
                        "transcription_failed",
                        None,
                        exc.message,
                    )
                except Exception:
                    status, transcript, error = (
                        "transcription_failed",
                        None,
                        "Transcription failed; retry from the preserved audio.",
                    )
            self.assert_write_scope()
        finally:
            snapshot.close()
        status = "routed" if record["destination_id"] else status
        self.db.execute(
            "UPDATE capability_knowledge_captures SET status=?,transcript=?,error=?,revision=revision+1 WHERE id=? AND transcript IS NULL",
            (status, transcript, error, identity),
        )
        self.db.commit()
        if refusal is not None:
            raise refusal
        return self.get(identity)

    async def route(self, identity, payload, *, _request=None):
        payload = dict(payload)
        if set(payload) != {
            "request_id",
            "revision",
            "destination",
            "title",
            "content",
        }:
            raise CaptureError(
                "Route requires request_id, revision, destination, title and content only"
            )
        key = request_key(payload["request_id"])
        if payload["destination"] not in {"note", "journal", "fleeting"}:
            raise CaptureError("destination must be note, journal or fleeting")
        text_field(payload["title"], "title", 300)
        text_field(payload["content"], "content", 100000)
        if type(payload["revision"]) is not int:
            raise CaptureError("revision must be an integer")
        current = self.get(identity)
        packed = json.dumps(payload, sort_keys=True)
        prior = self.db.execute(
            "SELECT * FROM capability_knowledge_capture_events WHERE request_id=?",
            (key,),
        ).fetchone()
        if prior:
            if prior["capture_id"] != identity or prior["payload"] != packed:
                raise CaptureError(
                    "request_id already belongs to a different route", 409
                )
            if prior["event"] == "routed":
                return current
        row = self.db.execute(
            "SELECT pending FROM capability_knowledge_captures WHERE id=?", (identity,)
        ).fetchone()
        if row["pending"] and row["pending"] != packed:
            raise CaptureError(
                "A previous route must be retried before another change", 409
            )
        if current["revision"] != payload["revision"]:
            raise CaptureError("Capture changed; reload before routing", 409)
        await _admit_text(
            payload["title"] + "\n" + payload["content"], "capture_route", _request
        )
        # The receipt may have changed while the child was reading the immutable copy.
        if self.get(identity)["revision"] != payload["revision"]:
            raise CaptureError("Capture changed; reload before routing", 409)
        self.assert_write_scope()
        self.db.execute(
            "UPDATE capability_knowledge_captures SET pending=? WHERE id=?",
            (packed, identity),
        )
        self.db.commit()
        destination_id = current["destination_id"]
        if destination_id and self.store.get_item(destination_id) is None:
            raise CaptureError(
                "Destination was removed; original capture is preserved", 409
            )
        if not destination_id:
            guid = f"capture:route:{identity}"
            existing = self.db.execute(
                "SELECT id FROM items WHERE guid=?", (guid,)
            ).fetchone()
            destination_id = (
                existing["id"]
                if existing
                else self.store.create_typed_item(
                    item_type=payload["destination"],
                    title=payload["title"],
                    content=payload["content"],
                    guid=guid,
                    extra={
                        "file_metadata": {
                            "capture_id": identity,
                            "original_at": current["captured_at"],
                        }
                    },
                )
            )
        item = self.store.get_item(destination_id)
        metadata = item.get("file_metadata") or {}
        self.store.update_item(
            destination_id,
            item_type=payload["destination"],
            title=payload["title"],
            content=payload["content"],
            file_metadata={
                **metadata,
                "capture_id": identity,
                "original_at": current["captured_at"],
            },
        )
        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "INSERT INTO capability_knowledge_capture_events VALUES (?,?,?,?,?)",
                (
                    identity,
                    key,
                    "routed",
                    packed,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.db.execute(
                "UPDATE capability_knowledge_captures SET destination_id=?,status='routed',revision=revision+1,pending=NULL WHERE id=?",
                (destination_id, identity),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.get(identity)
