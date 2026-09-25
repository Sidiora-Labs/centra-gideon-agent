import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.sdk.video import VideoControl, VideoGenModel, VideoResult
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.images import ImageService
from gideon.workspace.capabilities.media.images_http import register_images
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider
from gideon.workspace.capabilities.media.videos import VideoService, probe_video


@pytest.fixture
def service(tmp_path):
    return VideoService(ImageService(NativeArtifactProvider(tmp_path / "artifacts")))


def clip(tmp_path, color="red"):
    path = tmp_path / (color + ".mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=32x24:r=4:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=20,
    )
    return path


def image(service, color=(3, 20, 70, 190)):
    buffer = io.BytesIO()
    Image.new("RGBA", (12, 16), color).save(buffer, "PNG")
    artifact = service.artifacts.create_binary(
        name="Frame", data=buffer.getvalue(), mime="image/png"
    )
    return artifact, buffer.getvalue()


def request(service, **kwargs):
    return service.prepare(dict(prompt="A moving scene", duration_seconds=4, **kwargs))


def jobs_for(service, tmp_path):
    return MediaJobs(
        tmp_path / "jobs.sqlite3",
        SketchStore(tmp_path / "sketches.sqlite3", service.artifacts),
        videos=service,
    )


def test_optional_provider_contract_preserves_old_positional_construction():
    model = VideoGenModel("old", "description", ["16:9"], 10, True, False)
    assert model.name == "old"
    assert model.aspect_ratios == ["16:9"]
    assert model.max_duration_s == 10
    assert not model.supports_first_frame
    assert not model.supports_last_frame
    assert not model.supports_continuation
    assert model.supported_controls == {}
    assert model.durations == []
    control = VideoControl(0, 100, True)
    assert control.minimum == 0
    assert control.maximum == 100
    assert control.integer is True


def test_first_and_last_frames_pin_real_canonical_pixels(service, tmp_path):
    first, raw = image(service)
    last, last_raw = image(service, (200, 30, 40, 0))
    body = request(
        service,
        first_frame_artifact_id=first.slug,
        first_frame_version=1,
        last_frame_artifact_id=last.slug,
        last_frame_version=1,
    )
    assert body["prompt"] == "A moving scene"
    assert body["duration_seconds"] == 4
    assert body["controls"] == {}
    assert body["aspect_ratio"] == ""
    paths = service.stage(body, tmp_path)
    assert set(paths) == {"first_frame", "last_frame"}
    with Image.open(paths["first_frame"]) as frame:
        assert frame.size == (12, 16)
        assert frame.getpixel((0, 0)) == (3, 20, 70, 190)
    with Image.open(paths["last_frame"]) as frame:
        assert frame.getpixel((0, 0)) == (200, 30, 40, 0)
    assert service.artifacts.raw_bytes(first.slug, version=1)[0] == raw
    assert service.artifacts.raw_bytes(last.slug, version=1)[0] == last_raw
    assert service.artifacts.get(first.slug).version == 1


def test_continuation_extracts_actual_last_frame_without_rewriting_video(
    service, tmp_path
):
    path = clip(tmp_path)
    raw = path.read_bytes()
    source = service.artifacts.create_binary(
        name="Clip", data=raw, mime="video/mp4", kind="video"
    )
    body = request(
        service, continuation_artifact_id=source.slug, continuation_version=1
    )
    assert body["continuation_version"] == 1
    staged = service.stage(body, tmp_path)
    assert Path(staged["continuation_video"]).read_bytes() == raw
    with Image.open(staged["continuation_frame"]) as frame:
        assert frame.size == (32, 24)
        red, green, blue = frame.convert("RGB").getpixel((0, 0))
        assert red > 240
        assert green < 10
        assert blue < 10
    assert service.artifacts.raw_bytes(source.slug)[0] == raw
    assert service.artifacts.get(source.slug).version == 1
    assert probe_video(path)["duration_seconds"] == 1


def test_materialization_decodes_real_video_and_retains_exact_provenance(
    service, tmp_path
):
    path = clip(tmp_path)
    prepared = request(service)
    result = service.materialize(
        VideoResult(local_path=str(path)), prepared, "video-output"
    )
    assert result["width"] == 32
    assert result["height"] == 24
    assert result["duration_seconds"] == 1
    assert result["version"] == 1
    artifact = service.artifacts.get(result["artifact_id"])
    assert artifact.kind == "video"
    assert service.artifacts.raw_bytes(artifact.slug)[0] == path.read_bytes()
    metadata = artifact.events[0].metadata
    assert metadata["media_job_id"] == "video-output"
    assert metadata["model_selection"] == prepared["selection"]
    assert (
        metadata["video_request_sha256"]
        == hashlib.sha256(json.dumps(prepared, sort_keys=True).encode()).hexdigest()
    )
    assert (
        service.materialize(VideoResult(local_path=str(path)), prepared, "video-output")
        == result
    )
    assert len(service.artifacts.list()) == 1
    with pytest.raises(SketchError, match="identity conflicts"):
        service.materialize(
            VideoResult(local_path=str(path)),
            dict(prepared, prompt="Changed"),
            "video-output",
        )


def test_continuation_output_references_immutable_source(service, tmp_path):
    path = clip(tmp_path)
    source = service.artifacts.create_binary(
        name="Source", data=path.read_bytes(), mime="video/mp4", kind="video"
    )
    prepared = request(
        service, continuation_artifact_id=source.slug, continuation_version=1
    )
    result = service.materialize(
        VideoResult(local_path=str(path)), prepared, "continued"
    )
    metadata = service.artifacts.get(result["artifact_id"]).events[0].metadata
    assert metadata["source_artifact_id"] == source.slug
    assert metadata["source_version"] == 1
    assert result["artifact_id"] != source.slug
    assert service.artifacts.get(source.slug).version == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"prompt": ""},
        {"prompt": 4},
        {"prompt": "a" * 4001},
        {"duration_seconds": True},
        {"duration_seconds": 0},
        {"duration_seconds": 61},
        {"duration_seconds": float("nan")},
        {"duration_seconds": float("inf")},
        {"aspect_ratio": []},
        {"home": "/tmp/other"},
        {"controls": {"seed": True}},
        {"controls": {"seed": 1.5}},
        {"controls": {"motion": -1}},
        {"controls": {"guidance": float("nan")}},
        {"controls": {"unknown": 1}},
        {"first_frame_artifact_id": "missing"},
        {"continuation_version": 1},
        {"last_frame_artifact_id": {}, "last_frame_version": 1},
        {"controls": []},
    ],
)
def test_invalid_input_fails_before_job_or_provider(service, patch):
    with pytest.raises(SketchError):
        service.prepare(dict({"prompt": "scene", "duration_seconds": 4}, **patch))
    assert service.artifacts.list() == []


def test_missing_pinned_references_and_wrong_kinds(service):
    frame, raw = image(service)
    with pytest.raises(SketchError):
        request(service, first_frame_artifact_id=frame.slug, first_frame_version=9)
    with pytest.raises(SketchError):
        request(service, continuation_artifact_id=frame.slug, continuation_version=1)
    with pytest.raises(SketchError):
        service.source([], 1)
    with pytest.raises(SketchError):
        service.source("missing", True)
    assert service.artifacts.raw_bytes(frame.slug)[0] == raw


def test_model_capabilities_reject_unadvertised_controls_and_conditioning(service):
    legacy = VideoGenModel("legacy", aspect_ratios=["16:9"], max_duration_s=8)
    body = request(service)
    service.validate_model(body, legacy)
    for changes in (
        {"duration_seconds": 10},
        {"aspect_ratio": "1:1"},
        {"controls": {"seed": 1}},
        {"first_frame_artifact_id": "a"},
        {"last_frame_artifact_id": "a"},
        {"continuation_artifact_id": "a"},
    ):
        with pytest.raises(SketchError):
            service.validate_model(dict(body, **changes), legacy)
    model = VideoGenModel(
        "supported",
        max_duration_s=8,
        durations=[4, 8],
        supported_controls={"seed": VideoControl(0, 10, True)},
    )
    service.validate_model(dict(body, controls={"seed": 10}), model)
    with pytest.raises(SketchError):
        service.validate_model(dict(body, controls={"seed": 11}), model)
    with pytest.raises(SketchError):
        service.validate_model(dict(body, controls={"seed": 1.5}), model)
    with pytest.raises(SketchError):
        service.validate_model(dict(body, duration_seconds=6), model)


def test_bad_output_bytes_and_container_declarations_never_publish(service, tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_text("not a media container")
    with pytest.raises(SketchError):
        probe_video(path)
    with pytest.raises(SketchError):
        service.materialize(
            VideoResult(local_path=str(path)), request(service), "broken"
        )
    valid = clip(tmp_path)
    with pytest.raises(SketchError):
        service.materialize(
            VideoResult(local_path=str(valid), mime="video/webm"),
            request(service),
            "wrong-mime",
        )
    assert service.artifacts.list() == []


def test_unconfigured_real_worker_records_failure_and_keeps_retry_history(
    service, tmp_path
):
    jobs = jobs_for(service, tmp_path)
    body = {
        "operation": "video_generate",
        "request_id": "absent",
        "input": {"prompt": "scene", "duration_seconds": 4},
    }
    job = jobs.submit(body)
    assert jobs.submit(body)["id"] == job["id"]
    assert job["operation"] == "video_generate"
    assert job["status"] == "queued"
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    failed = jobs.get(job["id"])
    assert failed["status"] == "failed"
    assert "configured" in failed["error"]
    assert failed["result"] is None
    assert "owner_pid" not in failed
    retried = jobs.retry(job["id"], {"state_revision": failed["state_revision"]})
    assert retried["status"] == "queued"
    assert retried["events"][-2]["error"] == failed["error"]
    with pytest.raises(SketchError):
        jobs.cancel(job["id"], {"state_revision": failed["state_revision"]})
    cancelled = jobs.cancel(job["id"], {"state_revision": retried["state_revision"]})
    assert cancelled["status"] == "cancelled"
    assert service.artifacts.list() == []


@pytest.mark.asyncio
async def test_http_and_native_tools_share_real_queue(service, tmp_path):
    jobs = jobs_for(service, tmp_path)
    app = web.Application()
    register_jobs(app, jobs)
    register_images(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/capabilities/media/videos")
        assert response.status == 200
        capability = await response.json()
        assert capability["available"] is False
        assert capability["media_tools_available"] is True
        assert capability["models"] == []
        denied = await client.get("/api/capabilities/media/videos?home=other")
        assert denied.status == 400
        queued = await client.post(
            "/api/capabilities/media/videos",
            json={
                "operation": "video_generate",
                "request_id": "http",
                "input": {"prompt": "scene", "duration_seconds": 4},
            },
        )
        assert queued.status == 202
        row = await queued.json()
        assert jobs.get(row["id"])["status"] == "queued"
        tool = MediaToolProvider(jobs.sketches, MediaLibrary(service.artifacts))
        read = await tool.invoke("media_jobs_get", {"job_id": row["id"]})
        assert read.success
        assert json.loads(read.output)["operation"] == "video_generate"
        submitted = await tool.invoke(
            "media_video_submit",
            {
                "request_id": "tool",
                "input": {"prompt": "tool scene", "duration_seconds": 4},
            },
        )
        assert submitted.success
        toolrow = json.loads(submitted.output)
        fetched = await client.get("/api/capabilities/media/jobs/" + toolrow["id"])
        assert (await fetched.json())["input"]["prompt"] == "tool scene"
        caps = await tool.invoke("media_video_capabilities", {})
        assert caps.success
        assert json.loads(caps.output)["available"] is False
        rejected = await tool.invoke(
            "media_video_submit",
            {
                "request_id": "bad",
                "input": {"prompt": "bad", "duration_seconds": 4, "provider": "other"},
            },
        )
        assert rejected.success is False
        assert len(jobs.list()["items"]) == 2
