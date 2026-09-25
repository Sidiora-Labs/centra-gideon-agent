import asyncio
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.downloads import (
    JobCancellation,
    SourceDownloader,
    capabilities,
)
from gideon.workspace.capabilities.media.downloads_http import register_downloads
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider

URL = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture
def jobs(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    sketches = SketchStore(tmp_path / "sketches.sqlite3", artifacts)
    return MediaJobs(tmp_path / "jobs.sqlite3", sketches)


def request(kind="video", url=URL):
    return {"url": url, "kind": kind}


def submit(jobs, request_id="source-1", value=None):
    return jobs.submit(
        {
            "operation": "source_download",
            "request_id": request_id,
            "input": value or request(),
        }
    )


def test_prepare_accepts_bounded_single_youtube_sources(jobs):
    assert jobs.downloads.prepare(request()) == request()
    assert jobs.downloads.prepare(
        request("audio", "https://youtu.be/abcdefghijk?t=12")
    ) == {
        "url": "https://youtu.be/abcdefghijk?t=12",
        "kind": "audio",
    }
    assert (
        jobs.downloads.prepare(request(url="https://m.youtube.com/shorts/abcdefghijk"))[
            "kind"
        ]
        == "video"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=abcdefghijk",
        "https://youtube.com/playlist?list=abcdefghijk",
        "https://youtube.com/watch?v=short",
        "https://youtube.com.evil.test/watch?v=abcdefghijk",
        "https://user@youtube.com/watch?v=abcdefghijk",
        "https://127.0.0.1/watch?v=abcdefghijk",
        "file:///etc/passwd",
        "",
    ],
)
def test_prepare_refuses_non_video_or_untrusted_urls(jobs, url):
    with pytest.raises(SketchError, match="single HTTPS YouTube"):
        jobs.downloads.prepare(request(url=url))
    assert jobs.list()["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"url": URL},
        {"kind": "video"},
        {"url": URL, "kind": "captions"},
        {"url": URL, "kind": "video", "output": "/tmp/x"},
    ],
)
def test_prepare_rejects_missing_unknown_or_unsupported_fields(jobs, body):
    with pytest.raises(SketchError):
        jobs.downloads.prepare(body)
    assert jobs.sketches.artifacts.list() == []


def test_publish_creates_real_versioned_video_artifact_with_provenance(jobs):
    data = b"\x00\x00\x00\x18ftypisom" + b"video-payload"
    metadata = {"id": "abcdefghijk", "title": "A guarded source"}
    result = jobs.downloads.publish(request(), "job-1", metadata, data, "mp4", 42.5)
    assert result == {
        "artifact_id": "media-source-job-1",
        "version": 1,
        "kind": "video",
        "mime": "video/mp4",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "duration_seconds": 42.5,
        "source_url": URL,
        "source_external_id": "abcdefghijk",
    }
    artifact = jobs.sketches.artifacts.get(result["artifact_id"])
    assert artifact.kind == "video"
    assert artifact.source == "import"
    assert artifact.tags == ["download", "source", "video"]
    assert jobs.sketches.artifacts.raw_bytes(artifact.slug, version=1) == (
        data,
        "video/mp4",
    )
    event = artifact.events[0]
    assert event.metadata["media_job_id"] == "job-1"
    assert event.metadata["source_url"] == URL
    assert event.metadata["source_external_id"] == "abcdefghijk"
    assert event.metadata["download_sha256"] == result["sha256"]


def test_publish_creates_audio_artifact_and_library_projection(jobs):
    data = b"ID3" + b"audio-payload"
    result = jobs.downloads.publish(
        request("audio"),
        "job-audio",
        {"id": "abcdefghijk", "title": "Audio source"},
        data,
        "mp3",
        12,
    )
    assert result["kind"] == "audio"
    assert result["mime"] == "audio/mpeg"
    stored = jobs.sketches.artifacts.get(result["artifact_id"])
    assert stored.name == "Audio source"
    assert jobs.sketches.artifacts.raw_bytes(stored.slug) == (data, "audio/mpeg")


def test_publish_is_idempotent_but_rejects_identity_conflicts(jobs):
    data = b"\x00\x00\x00\x18ftypisomstable"
    meta = {"id": "abcdefghijk", "title": "Stable"}
    first = jobs.downloads.publish(request(), "stable", meta, data, "mp4", 10)
    assert jobs.downloads.publish(request(), "stable", meta, data, "mp4", 10) == first
    assert len(jobs.sketches.artifacts.list()) == 1
    with pytest.raises(SketchError, match="identity conflict"):
        jobs.downloads.publish(request(), "stable", meta, data + b"changed", "mp4", 10)
    with pytest.raises(SketchError, match="identity conflict"):
        jobs.downloads.publish(
            request(url="https://youtu.be/zyxwvutsrqp"), "stable", meta, data, "mp4", 10
        )
    assert jobs.sketches.artifacts.raw_bytes(first["artifact_id"])[0] == data


@pytest.mark.parametrize(
    "data,extension,kind",
    [
        (b"", "mp4", "video"),
        (b"bytes", "exe", "video"),
        (b"bytes", "mp4", "audio"),
        (b"x" * (33554432 + 1), "mp4", "video"),
    ],
)
def test_publish_rejects_empty_oversized_or_mismatched_results(
    jobs, data, extension, kind
):
    with pytest.raises(SketchError, match="empty, oversized or unsupported"):
        jobs.downloads.publish(
            request(kind), "bad", {"title": "Bad"}, data, extension, 2
        )
    assert jobs.sketches.artifacts.list() == []


def test_durable_job_replay_conflict_and_restart(jobs):
    first = submit(jobs)
    assert first["status"] == "queued"
    assert first["operation"] == "source_download"
    assert first["input"] == request()
    replay = submit(jobs)
    assert replay["id"] == first["id"]
    reopened = MediaJobs(jobs.path, jobs.sketches)
    assert reopened.get(first["id"])["input"] == request()
    with pytest.raises(SketchError, match="conflicts"):
        submit(reopened, value=request("audio"))
    assert len(reopened.list()["items"]) == 1


class RecordedReader:
    def __init__(self, cancelled, *, duration=9):
        self.cancelled = cancelled
        self.duration = duration
        self.calls = []

    def metadata(self, url):
        self.calls.append(("metadata", url))
        return {
            "id": "abcdefghijk",
            "title": "Fetched source",
            "duration": self.duration,
        }

    async def media(self, metadata, kind):
        self.calls.append(("media", metadata["id"], kind))
        if self.cancelled.is_set():
            raise SketchError("Video acquisition cancelled", 409)
        return b"\x00\x00\x00\x18ftypisomfetched", "mp4"


def test_worker_runs_download_and_retains_real_artifact(jobs):
    readers = []
    jobs.downloads.reader_factory = (
        lambda cancelled: readers.append(RecordedReader(cancelled)) or readers[-1]
    )
    queued = submit(jobs)
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    finished = jobs.get(queued["id"])
    assert finished["status"] == "succeeded"
    assert finished["attempt"] == 1
    assert finished["result"]["artifact_id"] == "media-source-" + queued["id"]
    assert readers[0].calls == [("metadata", URL), ("media", "abcdefghijk", "video")]
    raw = jobs.sketches.artifacts.raw_bytes(
        finished["result"]["artifact_id"], version=1
    )
    assert raw == (b"\x00\x00\x00\x18ftypisomfetched", "video/mp4")
    assert finished["events"][-1]["result"] == finished["result"]


def test_worker_records_duration_failure_without_artifact(jobs):
    jobs.downloads.reader_factory = lambda cancelled: RecordedReader(
        cancelled, duration=21601
    )
    queued = submit(jobs)
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    failed = jobs.get(queued["id"])
    assert failed["status"] == "failed"
    assert failed["result"] is None
    assert "six hours" in failed["error"]
    assert jobs.sketches.artifacts.list() == []


def test_job_cancellation_reflects_worker_and_job_state():
    stopped = False
    cancellation = JobCancellation(lambda: stopped)
    assert cancellation.is_set() is False
    stopped = True
    assert cancellation.is_set() is True
    cancellation = JobCancellation(lambda: False)
    cancellation.set()
    assert cancellation.is_set() is True


@pytest.mark.asyncio
async def test_http_submission_and_capabilities_use_existing_media_boundary(jobs):
    app = web.Application()
    register_jobs(app, jobs)
    register_downloads(app)
    async with TestClient(TestServer(app)) as client:
        available = await client.get("/api/capabilities/media/source-download")
        assert available.status == 200
        info = await available.json()
        assert info["transport"] in ("guarded-video-reader", "unavailable")
        assert info["max_artifact_bytes"] == 33554432
        assert info["max_duration_seconds"] == 21600
        assert info["kinds"] == ["video", "audio"]
        denied_query = await client.get(
            "/api/capabilities/media/source-download?probe=1"
        )
        assert denied_query.status == 400
        response = await client.post(
            "/api/capabilities/media/jobs",
            json={
                "operation": "source_download",
                "request_id": "http-source",
                "input": request(),
            },
        )
        assert response.status == 202
        queued = await response.json()
        loaded = await client.get("/api/capabilities/media/jobs/" + queued["id"])
        assert loaded.status == 200
        assert (await loaded.json())["input"] == request()
        unsafe = await client.post(
            "/api/capabilities/media/jobs",
            json={
                "operation": "source_download",
                "request_id": "unsafe",
                "input": request(url="http://127.0.0.1/video"),
            },
        )
        assert unsafe.status == 400
    assert len(jobs.list()["items"]) == 1


@pytest.mark.asyncio
async def test_native_tool_queues_same_durable_job(jobs):
    provider = MediaToolProvider(jobs.sketches, MediaLibrary(jobs.sketches.artifacts))
    result = await provider.invoke(
        "media_source_download_submit",
        {"request_id": "native-source", "input": request("audio")},
    )
    assert result.success
    queued = json.loads(result.output)
    assert queued["operation"] == "source_download"
    assert queued["input"] == request("audio")
    definitions = {item.name: item for item in await provider.list_tools()}
    definition = definitions["media_source_download_submit"]
    assert definition.requires_approval is False
    assert definition.parameters["additionalProperties"] is False
    assert set(definition.parameters["required"]) == {"request_id", "input"}
    denied = await provider.invoke(
        "media_source_download_submit",
        {
            "request_id": "native-bad",
            "input": request(url="https://example.test/video"),
        },
    )
    assert denied.success is False
    assert denied.metadata["status"] == 400


def test_capabilities_never_claims_external_download_success():
    value = capabilities()
    assert set(value) == {
        "available",
        "transport",
        "max_artifact_bytes",
        "max_duration_seconds",
        "kinds",
    }
    assert isinstance(value["available"], bool)
    assert value["transport"] in ("guarded-video-reader", "unavailable")
    assert "downloaded" not in value
