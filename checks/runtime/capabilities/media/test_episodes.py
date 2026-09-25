import asyncio
import copy
import io
import json
import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.episodes import EpisodeStore
from gideon.workspace.capabilities.media.episodes_http import register_episodes
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider
from gideon.workspace.capabilities.media.videos import probe_video


@pytest.fixture
def jobs(tmp_path):
    return MediaJobs(
        tmp_path / "jobs.sqlite3",
        SketchStore(
            tmp_path / "sketches.sqlite3",
            NativeArtifactProvider(tmp_path / "artifacts"),
        ),
    )


def clip(jobs, tmp_path, color):
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
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=20,
    )
    artifact = jobs.sketches.artifacts.create_binary(
        name=color, data=path.read_bytes(), mime="video/mp4", kind="video"
    )
    return artifact


def plan(jobs, tmp_path):
    artifacts = [clip(jobs, tmp_path, color) for color in ("red", "blue")]
    return dict(
        title="Episode",
        width=32,
        height=24,
        fps=4,
        aspect_ratio="16:9",
        scenes=[
            dict(
                prompt="Scene " + str(index),
                duration_seconds=1,
                mode="reuse",
                allow_fallback=False,
                artifact_id=artifact.slug,
                version=1,
            )
            for index, artifact in enumerate(artifacts)
        ],
        request_id="create",
    )


def generated_plan():
    return dict(
        title="Generated",
        width=32,
        height=24,
        fps=4,
        aspect_ratio="16:9",
        scenes=[
            dict(
                prompt="Establish",
                duration_seconds=4,
                mode="establish",
                allow_fallback=False,
            )
        ],
        request_id="generate",
    )


def queue(jobs, document, request_id="render"):
    return jobs.submit(
        dict(
            operation="episode_render",
            request_id=request_id,
            input=dict(episode_id=document["id"], revision=document["revision"]),
        )
    )


def pixel(path, second):
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(second),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )
    with Image.open(io.BytesIO(result.stdout)) as image:
        return image.convert("RGB").getpixel((10, 10))


def test_episode_revisions_preserve_script_order_and_source_pins(jobs, tmp_path):
    body = plan(jobs, tmp_path)
    first = jobs.episodes.save(body)
    assert first["revision"] == 1
    assert first["duration"] == 2
    assert first["updated_at"].endswith("+00:00")
    assert first["scenes"] == body["scenes"]
    assert jobs.episodes.save(body) == first
    second = jobs.episodes.save(
        dict(body, title="Revised", request_id="revision", revision=1), first["id"]
    )
    assert second["revision"] == 2
    assert jobs.episodes.get(first["id"]) == second
    assert jobs.episodes.get(first["id"], 1) == first
    assert jobs.episodes.history(first["id"])["items"] == [second, first]
    assert jobs.episodes.list()["items"] == [second]
    with pytest.raises(SketchError, match="revision changed"):
        jobs.episodes.save(dict(body, request_id="stale", revision=1), first["id"])
    with pytest.raises(SketchError, match="request ID conflict"):
        jobs.episodes.save(dict(body, title="Changed"))
    reopened = EpisodeStore(jobs.episodes.path, jobs.videos, jobs.timelines)
    assert reopened.get(first["id"], 1) == first
    assert reopened.get(first["id"])["title"] == "Revised"


def test_real_worker_assembles_reused_clips_with_lineage_and_provenance(jobs, tmp_path):
    body = plan(jobs, tmp_path)
    originals = {
        scene["artifact_id"]: jobs.sketches.artifacts.raw_bytes(scene["artifact_id"])[0]
        for scene in body["scenes"]
    }
    document = jobs.episodes.save(body)
    queued = queue(jobs, document)
    assert queue(jobs, document)["id"] == queued["id"]
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    job = jobs.get(queued["id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["progress"] == 1
    assert job["result"]["episode_id"] == document["id"]
    assert job["result"]["episode_revision"] == 1
    assert job["result"]["timeline_revision"] == 1
    path = tmp_path / "result.mp4"
    path.write_bytes(jobs.sketches.artifacts.raw_bytes(job["result"]["artifact_id"])[0])
    assert abs(probe_video(path)["duration_seconds"] - 2) < 0.1
    assert pixel(path, 0.25)[0] > 240
    assert pixel(path, 1.25)[2] > 240
    scenes = jobs.episodes.scenes(job["id"])["items"]
    assert len(scenes) == 2
    assert scenes[0]["predecessor"] is None
    assert scenes[1]["predecessor"] == dict(
        artifact_id=body["scenes"][0]["artifact_id"], version=1
    )
    assert [scene["status"] for scene in scenes] == ["succeeded", "succeeded"]
    assert [scene["attempts"] for scene in scenes] == [1, 1]
    assert scenes[1]["result"]["artifact_id"] == body["scenes"][1]["artifact_id"]
    assert [event["status"] for event in scenes[0]["events"]] == [
        "running",
        "succeeded",
    ]
    assert scenes[0].get("fallback", False) is False
    assert (
        jobs.timelines.get(job["result"]["timeline_id"])["segments"][1]["artifact_id"]
        == body["scenes"][1]["artifact_id"]
    )
    for artifact_id, raw in originals.items():
        assert jobs.sketches.artifacts.raw_bytes(artifact_id)[0] == raw
        assert jobs.sketches.artifacts.get(artifact_id).version == 1


@pytest.mark.asyncio
async def test_real_checkpoint_resume_keeps_completed_scene_attempt(jobs, tmp_path):
    document = jobs.episodes.save(plan(jobs, tmp_path))
    request = jobs.episodes.prepare(dict(episode_id=document["id"], revision=1))
    with pytest.raises(SketchError, match="cancelled"):
        await jobs.episodes.execute(
            request,
            "resume",
            lambda: len(jobs.episodes.scenes("resume")["items"]) == 1,
            lambda pid: None,
        )
    checkpoint = jobs.episodes.scenes("resume")["items"]
    assert len(checkpoint) == 1
    assert checkpoint[0]["status"] == "succeeded"
    assert checkpoint[0]["attempts"] == 1
    reopened = EpisodeStore(jobs.episodes.path, jobs.videos, jobs.timelines)
    pids, progress = [], []
    result = await reopened.execute(
        request, "resume", lambda: False, pids.append, progress.append
    )
    assert result["episode_revision"] == 1
    assert result["artifact_id"]
    assert len(pids) == 3
    assert progress[-1] == 1
    rows = reopened.scenes("resume")["items"]
    assert rows[0] == checkpoint[0]
    assert rows[1]["attempts"] == 1
    assert rows[1]["predecessor"]["artifact_id"] == rows[0]["result"]["artifact_id"]
    assert [row["status"] for row in rows] == ["succeeded", "succeeded"]


def test_actual_unconfigured_provider_fails_preflight_without_fake_generation(jobs):
    document = jobs.episodes.save(generated_plan())
    queued = queue(jobs, document)
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    failed = jobs.get(queued["id"])
    assert failed["status"] == "failed"
    assert "No video provider" in failed["error"]
    assert failed["result"] is None
    assert jobs.episodes.scenes(queued["id"])["items"] == []
    assert jobs.sketches.artifacts.list() == []
    assert jobs.timelines.list()["items"] == []
    assert jobs.episodes.get(document["id"])["scenes"][0]["prompt"] == "Establish"


@pytest.mark.parametrize(
    "patch",
    [
        {"title": ""},
        {"width": 3},
        {"height": 2048},
        {"fps": True},
        {"fps": 61},
        {"aspect_ratio": []},
        {"scenes": []},
        {"scenes": {}},
        {"provider": "other"},
        {"request_id": []},
        {"revision": True},
    ],
)
def test_invalid_plan_has_no_durable_effect(jobs, patch):
    with pytest.raises(SketchError):
        jobs.episodes.save(dict(generated_plan(), **patch))
    assert jobs.episodes.list()["items"] == []
    assert jobs.list()["items"] == []


@pytest.mark.parametrize(
    "patch",
    [
        {"prompt": ""},
        {"prompt": "a" * 4001},
        {"duration_seconds": float("nan")},
        {"duration_seconds": True},
        {"duration_seconds": 0},
        {"duration_seconds": 61},
        {"mode": "continue"},
        {"mode": "invalid"},
        {"allow_fallback": "yes"},
        {"artifact_id": "override"},
        {"path": "/private/file"},
        {"mode": "reuse"},
    ],
)
def test_invalid_scene_lint_prevents_provider_work(jobs, patch):
    body = generated_plan()
    body["scenes"][0].update(patch)
    with pytest.raises(SketchError):
        jobs.episodes.save(body)
    assert jobs.episodes.list()["items"] == []
    assert jobs.episodes.scenes("not-started")["items"] == []


def test_partial_reuse_cannot_claim_continuity_from_wrong_final_frame(jobs, tmp_path):
    body = plan(jobs, tmp_path)
    body["scenes"][0]["duration_seconds"] = 0.5
    body["scenes"][1] = dict(
        prompt="Continue", duration_seconds=4, mode="continue", allow_fallback=False
    )
    with pytest.raises(SketchError, match="trimmed reused clip"):
        jobs.episodes.save(body)
    assert jobs.episodes.list()["items"] == []
    body["scenes"][0]["duration_seconds"] = 1
    saved = jobs.episodes.save(body)
    assert saved["duration"] == 5
    assert saved["scenes"][1]["mode"] == "continue"


def test_reuse_bounds_and_pins_are_authoritative(jobs, tmp_path):
    body = plan(jobs, tmp_path)
    changed = copy.deepcopy(body)
    changed["scenes"][0]["duration_seconds"] = 2
    with pytest.raises(SketchError, match="duration"):
        jobs.episodes.save(changed)
    changed["scenes"][0]["duration_seconds"] = 1
    changed["scenes"][0]["version"] = 9
    with pytest.raises(SketchError):
        jobs.episodes.save(changed)
    changed["scenes"][0]["artifact_id"] = []
    with pytest.raises(SketchError):
        jobs.episodes.save(changed)
    assert jobs.episodes.list()["items"] == []


def test_scene_events_preserve_explicit_fallback_and_attempt_history(jobs):
    first = jobs.episodes.record(
        "history",
        0,
        "running",
        mode="continue",
        predecessor={"artifact_id": "source", "version": 1},
    )
    assert first["attempts"] == 1
    fallback = jobs.episodes.record(
        "history",
        0,
        "fallback",
        fallback=True,
        detail="Policy permits fresh establishment",
    )
    assert fallback["fallback"] is True
    failed = jobs.episodes.record("history", 0, "failed", error="Unavailable")
    assert failed["error"] == "Unavailable"
    retried = jobs.episodes.record("history", 0, "running", error=None)
    assert retried["attempts"] == 2
    assert [row["status"] for row in retried["events"]] == [
        "running",
        "fallback",
        "failed",
        "running",
    ]
    assert retried["events"][2]["error"] == "Unavailable"
    assert retried["predecessor"]["version"] == 1
    assert jobs.episodes.scenes("history")["items"][0] == retried


@pytest.mark.asyncio
async def test_http_and_native_consumers_share_plans_jobs_and_scene_ledger(
    jobs, tmp_path
):
    app = web.Application()
    register_jobs(app, jobs)
    register_episodes(app)
    body = plan(jobs, tmp_path)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/capabilities/media/episodes", json=body)
        assert response.status == 201
        document = await response.json()
        tool = MediaToolProvider(jobs.sketches, MediaLibrary(jobs.sketches.artifacts))
        read = await tool.invoke("media_episodes_get", {"episode_id": document["id"]})
        assert read.success
        assert json.loads(read.output) == document
        saved = await tool.invoke(
            "media_episodes_save",
            dict(
                body,
                episode_id=document["id"],
                revision=1,
                request_id="tool-save",
                title="Updated",
            ),
        )
        assert saved.success
        assert json.loads(saved.output)["revision"] == 2
        history = await client.get(
            "/api/capabilities/media/episodes/" + document["id"] + "/history"
        )
        assert [item["revision"] for item in (await history.json())["items"]] == [2, 1]
        queued = await tool.invoke(
            "media_episode_render",
            {
                "request_id": "tool-render",
                "input": {"episode_id": document["id"], "revision": 1},
            },
        )
        assert queued.success
        job = json.loads(queued.output)
        scenes = await client.get(
            "/api/capabilities/media/jobs/" + job["id"] + "/scenes"
        )
        assert scenes.status == 200
        assert await scenes.json() == {"items": []}
        native = await tool.invoke("media_episode_scenes", {"job_id": job["id"]})
        assert native.success
        assert json.loads(native.output)["items"] == []
        malformed = await tool.invoke(
            "media_episodes_save", dict(body, provider="other")
        )
        assert not malformed.success
        denied = await client.get("/api/capabilities/media/episodes?home=other")
        assert denied.status == 400
        missing = await client.get("/api/capabilities/media/jobs/missing/scenes")
        assert missing.status == 404
        stale = await client.put(
            "/api/capabilities/media/episodes/" + document["id"],
            json=dict(body, revision=1, request_id="stale"),
        )
        assert stale.status == 409
        assert len(jobs.episodes.list()["items"]) == 1
