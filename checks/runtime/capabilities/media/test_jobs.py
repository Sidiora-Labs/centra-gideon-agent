import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker, identity
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def jobs(tmp_path):
    sketches = SketchStore(
        tmp_path / "capabilities/media/sketches.sqlite3",
        NativeArtifactProvider(tmp_path / "artifacts"),
    )
    return MediaJobs(tmp_path / "capabilities/media/jobs.sqlite3", sketches)


def submit(jobs, request="first"):
    sketch = jobs.sketches.create(
        dict(
            width=12,
            height=12,
            request_id=request,
            strokes=[
                dict(tool="draw", color="#ff0000", width=3, points=[[3, 3], [8, 8]])
            ],
        )
    )
    return jobs.submit(
        dict(
            operation="sketch_export",
            sketch_id=sketch["id"],
            revision=sketch["revision"],
            request_id=request,
        )
    )


def guard(job):
    return {"state_revision": job["state_revision"]}


def context():
    return WorkerContext("gideon-media", "default", WorkerControl())


def test_real_render_and_reopen_preserve_pixels_and_public_metadata(jobs):
    queued = submit(jobs)
    assert queued["status"] == "queued"
    assert queued["attempt"] == 0
    assert queued["state_revision"] == 1
    assert "owner_pid" not in queued
    assert "owner_identity" not in queued
    MediaWorker(jobs).run_once(context())
    finished = jobs.get(queued["id"])
    assert finished["status"] == "succeeded"
    assert finished["attempt"] == 1
    assert finished["state_revision"] == 3
    assert [event["status"] for event in finished["events"]] == [
        "queued",
        "running",
        "succeeded",
    ]
    assert finished["events"][-1]["result"] == finished["result"]
    assert finished["error"] is None
    raw, mime = jobs.sketches.artifacts.raw_bytes(finished["result"]["artifact_id"])
    assert mime == "image/png"
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    assert image.size == (12, 12)
    assert image.getpixel((5, 5))[:3] == (255, 0, 0)
    assert jobs.sketches.get(queued["sketch_id"])["revision"] == queued["revision"]
    reopened = MediaJobs(jobs.path, jobs.sketches)
    assert reopened.get(queued["id"]) == finished
    assert reopened.list()["items"] == [finished]
    assert "owner_pid" not in json.dumps(finished)
    assert "owner_identity" not in json.dumps(finished)
    MediaWorker(reopened).run_once(context())
    assert reopened.get(queued["id"]) == finished


def test_replay_conflict_and_unknown_engine(jobs):
    first = submit(jobs)
    args = dict(
        operation="sketch_export",
        sketch_id=first["sketch_id"],
        revision=first["revision"],
        request_id="first",
    )
    assert jobs.submit(args) == first
    with pytest.raises(SketchError):
        jobs.submit(dict(args, operation="external_gpu"))
    with pytest.raises(SketchError):
        jobs.submit(dict(args, owner_pid=1))
    with pytest.raises(SketchError):
        jobs.submit(dict(args, revision=True))
    with pytest.raises(SketchError):
        jobs.submit(dict(args, request_id=""))
    other = jobs.sketches.create(dict(width=4, height=4, request_id="other"))
    with pytest.raises(SketchError) as conflict:
        jobs.submit(dict(args, sketch_id=other["id"]))
    assert conflict.value.status == 409
    assert jobs.list()["items"] == [first]


def test_cancel_retry_cas_and_attempt_history(jobs):
    first = submit(jobs)
    cancelled = jobs.cancel(first["id"], guard(first))
    assert cancelled["status"] == "cancelled"
    assert jobs.claim() is None
    assert jobs.cancel(first["id"], guard(cancelled)) == cancelled
    queued = jobs.retry(first["id"], guard(cancelled))
    assert queued["status"] == "queued"
    with pytest.raises(SketchError) as stale:
        jobs.cancel(first["id"], guard(first))
    assert stale.value.status == 409
    with pytest.raises(SketchError):
        jobs.retry(first["id"], guard(cancelled))
    running = jobs.claim()
    assert running["attempt"] == 1
    cancelling = jobs.cancel(first["id"], guard(running))
    assert cancelling["status"] == "cancel_requested"
    output = jobs.sketches.export(first["sketch_id"], {"revision": first["revision"]})
    finished = jobs.finish(first["id"], result=output)
    assert finished["status"] == "cancelled"
    assert finished["result"] == output
    retried = jobs.retry(first["id"], guard(finished))
    assert retried["result"] is None
    assert retried["events"][-2]["result"] == output
    assert retried["events"][-2]["attempt"] == 1
    assert jobs.sketches.artifacts.raw_bytes(output["artifact_id"])[1] == "image/png"
    MediaWorker(jobs).run_once(context())
    success = jobs.get(first["id"])
    assert success["attempt"] == 2
    assert success["status"] == "succeeded"
    assert success["result"] == output
    with pytest.raises(SketchError):
        jobs.retry(first["id"], guard(success))


def test_real_revision_change_fails_then_records_retry_error(jobs):
    first = submit(jobs)
    jobs.sketches.update(
        first["sketch_id"], {"revision": first["revision"], "strokes": []}
    )
    MediaWorker(jobs).run_once(context())
    failed = jobs.get(first["id"])
    assert failed["status"] == "failed"
    assert failed["error"]
    assert failed["result"] is None
    jobs.retry(first["id"], guard(failed))
    MediaWorker(jobs).run_once(context())
    second = jobs.get(first["id"])
    assert second["attempt"] == 2
    assert second["status"] == "failed"
    assert second["events"][2]["error"] == failed["error"]
    assert len(jobs.sketches.artifacts.list()) == 0


def test_claim_is_atomic_across_connections(jobs):
    first = submit(jobs)
    stores = [MediaJobs(jobs.path, jobs.sketches) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda store: store.claim(), stores))
    winners = [claim for claim in claims if claim]
    assert len(winners) == 1
    assert winners[0]["id"] == first["id"]
    assert jobs.get(first["id"])["attempt"] == 1
    assert jobs.recover() == []
    internal = jobs.get(first["id"], internal=True)
    assert internal["owner_pid"] == os.getpid()
    assert internal["owner_identity"] == identity(os.getpid())
    assert internal["owner_identity"]
    jobs.finish(first["id"], error="Explicit interrupted attempt")
    with pytest.raises(SketchError):
        jobs.finish(first["id"])


def test_actual_dead_child_recovery_and_no_live_child_recovery(jobs):
    first = submit(jobs)
    code = """import sys,time
from pathlib import Path
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.sketches import SketchStore
from gideon.workspace.capabilities.media.jobs import MediaJobs
home=Path(sys.argv[1]); sketches=SketchStore(home/'capabilities/media/sketches.sqlite3', NativeArtifactProvider(home/'artifacts'))
jobs=MediaJobs(home/'capabilities/media/jobs.sqlite3', sketches)
print(jobs.claim()['id'], flush=True)
time.sleep(20)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(jobs.path.parents[2])],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == first["id"]
        assert jobs.get(first["id"])["status"] == "running"
        assert jobs.recover() == []
        internal = jobs.get(first["id"], internal=True)
        assert internal["owner_pid"] == child.pid
        assert internal["owner_identity"] == identity(child.pid)
        child.terminate()
        child.wait(timeout=5)
        recovered = jobs.recover()
        assert len(recovered) == 1
        assert recovered[0]["status"] == "failed"
        assert "exited" in recovered[0]["error"]
        assert "owner_pid" not in recovered[0]
        assert jobs.recover() == []
        queued = jobs.retry(first["id"], guard(recovered[0]))
        assert queued["status"] == "queued"
        MediaWorker(jobs).run_once(context())
        assert jobs.get(first["id"])["status"] == "succeeded"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_sdk_native_worker_real_process_and_denied_grant(jobs, tmp_path):
    first = submit(jobs)
    worker = (
        Path(__file__).resolve().parents[4]
        / "runtime/gideon/extensions/apps/native/gideon-media/worker.py"
    )
    env = dict(
        os.environ,
        GIDEON_HOME=str(jobs.path.parents[2]),
        GIDEON_APP_NAME="gideon-media",
        GIDEON_APP_WORKER_GRANT="backgroundTasks",
    )
    child = subprocess.Popen(
        [sys.executable, str(worker)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            time.monotonic() < deadline
            and jobs.get(first["id"])["status"] != "succeeded"
            and child.poll() is None
        ):
            time.sleep(0.05)
        assert jobs.get(first["id"])["status"] == "succeeded"
        assert child.poll() is None
        child.terminate()
        child.wait(timeout=5)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    denied_home = tmp_path / "denied"
    env.update(GIDEON_HOME=str(denied_home), GIDEON_APP_WORKER_GRANT="")
    denied = subprocess.run(
        [sys.executable, str(worker)],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert denied.returncode != 0
    assert not (denied_home / "capabilities").exists()
    assert not (denied_home / "artifacts").exists()
    env.update(GIDEON_APP_NAME="another-app", GIDEON_APP_WORKER_GRANT="backgroundTasks")
    wrong_app = subprocess.run(
        [sys.executable, str(worker)],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert wrong_app.returncode != 0
    assert not denied_home.exists()


@pytest.mark.asyncio
async def test_http_and_tools_use_same_durable_queue(jobs):
    first = submit(jobs)
    app = web.Application()
    register_jobs(app, jobs)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/media/jobs"
        response = await client.get(base)
        assert response.status == 200
        assert (await response.json())["items"] == [first]
        cancelled = await client.post(
            base + "/" + first["id"] + "/cancel", json=guard(first)
        )
        assert cancelled.status == 202
        item = await cancelled.json()
        assert item["status"] == "cancelled"
        assert "owner_pid" not in item
        for payload in ({}, {"state_revision": True}, dict(guard(item), home="/tmp")):
            response = await client.post(
                base + "/" + first["id"] + "/retry", json=payload
            )
            assert response.status == 400
        response = await client.get(base + "?runtime=other")
        assert response.status == 400
        response = await client.get(base + "/missing")
        assert response.status == 404
        provider = MediaToolProvider(
            jobs.sketches, MediaLibrary(jobs.sketches.artifacts)
        )
        result = await provider.invoke(
            "media_jobs_retry", dict(job_id=first["id"], **guard(item))
        )
        assert result.success
        assert json.loads(result.output)["status"] == "queued"
        MediaWorker(jobs).run_once(context())
        response = await client.get(base + "/" + first["id"])
        complete = await response.json()
        assert complete["status"] == "succeeded"
        result = await provider.invoke("media_jobs_get", {"job_id": first["id"]})
        assert result.success
        assert json.loads(result.output) == complete
        result = await provider.invoke("media_jobs_list", {})
        assert json.loads(result.output)["items"] == [complete]
        result = await provider.invoke(
            "media_jobs_cancel",
            {"job_id": first["id"], "state_revision": 1, "home": "/tmp"},
        )
        assert not result.success
