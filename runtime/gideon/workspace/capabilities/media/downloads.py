import asyncio
import hashlib
import importlib.util
import json
import re
from threading import Event

from .sketches import SketchError, fields

MIME = {
    ("video", "mp4"): "video/mp4",
    ("video", "webm"): "video/webm",
    ("audio", "m4a"): "audio/mp4",
    ("audio", "mp3"): "audio/mpeg",
    ("audio", "ogg"): "audio/ogg",
    ("audio", "webm"): "audio/ogg",
}


def capabilities():
    try:
        transport = (
            importlib.util.find_spec(
                "gideon.workspace.capabilities.knowledge.video_fetch"
            )
            is not None
        )
    except ModuleNotFoundError:
        transport = False
    return {
        "available": transport and importlib.util.find_spec("yt_dlp") is not None,
        "transport": "guarded-video-reader" if transport else "unavailable",
        "max_artifact_bytes": 33554432,
        "max_duration_seconds": 21600,
        "kinds": ["video", "audio"],
    }


class JobCancellation(Event):
    def __init__(self, stopped):
        super().__init__()
        self.stopped = stopped

    def is_set(self):
        return super().is_set() or self.stopped()


class SourceDownloader:
    def __init__(self, artifacts, reader_factory=None):
        self.artifacts = artifacts
        self.reader_factory = reader_factory or self._reader

    @staticmethod
    def _reader(cancelled):
        try:
            from gideon.workspace.capabilities.knowledge.video_fetch import VideoReader
        except ImportError as exc:
            raise SketchError("Guarded video acquisition is unavailable", 503) from exc
        return VideoReader("dashboard:media-source", cancelled)

    def prepare(self, body):
        fields(body, ("url", "kind"), ("url", "kind"))
        url, kind = body["url"], body["kind"]
        if (
            not isinstance(url, str)
            or len(url) > 2000
            or not re.fullmatch(
                r"https://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^#\s]*v=[A-Za-z0-9_-]{6,}|shorts/[A-Za-z0-9_-]{6,})|youtu\.be/[A-Za-z0-9_-]{6,})(?:[^\s]*)?",
                url,
                re.IGNORECASE,
            )
        ):
            raise SketchError("A single HTTPS YouTube video URL is required")
        if kind not in ("video", "audio"):
            raise SketchError("Download kind must be video or audio")
        return {"url": url, "kind": kind}

    async def execute(self, request, job_id, stopped):
        reader = self.reader_factory(JobCancellation(stopped))
        metadata = await asyncio.to_thread(reader.metadata, request["url"])
        duration = float(metadata.get("duration") or 0)
        if not 0 < duration <= 21600:
            raise SketchError("Source duration must be between zero and six hours", 413)
        data, extension = await reader.media(metadata, request["kind"])
        return await asyncio.to_thread(
            self.publish, request, job_id, metadata, data, extension, duration
        )

    def publish(self, request, job_id, metadata, data, extension, duration):
        kind = request["kind"]
        mime = MIME.get((kind, str(extension).lower()))
        if not mime or not data or len(data) > 33554432:
            raise SketchError("Downloaded media is empty, oversized or unsupported")
        digest = hashlib.sha256(data).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        slug = "media-source-" + job_id
        existing = self.artifacts.get(slug)
        if existing:
            event = existing.events[0] if existing.events else None
            if (
                event
                and event.metadata.get("download_sha256") == digest
                and event.metadata.get("source_request_sha256") == fingerprint
            ):
                return self.result(
                    existing, digest, len(data), duration, request["url"], metadata
                )
            raise SketchError("Downloaded source artifact identity conflict", 409)
        artifact = self.artifacts.create_binary(
            name=str(metadata.get("title") or "Downloaded media").strip()[:200],
            slug=slug,
            data=data,
            mime=mime,
            kind=kind,
            source="import",
            tags=["download", "source", kind],
            description="Guarded public media source download",
            event_metadata={
                "media_job_id": job_id,
                "source_url": request["url"],
                "source_external_id": str(metadata.get("id") or "")[:256],
                "download_sha256": digest,
                "source_request_sha256": fingerprint,
                "duration_seconds": str(duration),
            },
        )
        return self.result(
            artifact, digest, len(data), duration, request["url"], metadata
        )

    @staticmethod
    def result(artifact, digest, size, duration, url, metadata):
        return {
            "artifact_id": artifact.slug,
            "version": artifact.version,
            "kind": artifact.kind,
            "mime": artifact.mime,
            "sha256": digest,
            "bytes": size,
            "duration_seconds": duration,
            "source_url": url,
            "source_external_id": str(metadata.get("id") or ""),
        }
