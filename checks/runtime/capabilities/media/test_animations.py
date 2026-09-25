import asyncio
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.animations import AnimationService
from gideon.workspace.capabilities.media.animations_http import POLICY, register_animations
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def jobs(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    sketches = SketchStore(tmp_path / "sketches.sqlite3", artifacts)
    return MediaJobs(tmp_path / "jobs.sqlite3", sketches)


def request(**patch):
    return dict(
        {
            "title": "Orbital Bloom",
            "concept": "Three rings unfold into a flower and return to rest.",
            "renderer": "canvas2d",
            "duration_seconds": 12,
            "width": 1280,
            "height": 720,
            "fps": 30,
            "interactive": False,
        },
        **patch,
    )


def html(meta=None, body="ctx.fillRect(0,0,10,10)"):
    values = meta or {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "durationSeconds": 12,
        "renderer": "canvas2d",
        "interactive": False,
    }
    return (
        "<!doctype html><html><head><style>canvas{display:block}</style></head>"
        "<body><canvas></canvas><script>"
        f"window.ANIMATION_META={json.dumps(values)};"
        "const ctx=document.querySelector('canvas').getContext('2d');"
        f"function renderFrame(t){{ctx.clearRect(0,0,1280,720);{body}}}"
        "renderFrame(0);</script></body></html>"
    )


def queued(jobs, value=None, request_id="animation"):
    return jobs.submit(
        {
            "operation": "code_animation_generate",
            "request_id": request_id,
            "input": value or request(),
        }
    )


def test_prepare_builds_bounded_original_animation_contract(jobs):
    prepared = jobs.animations.prepare(request())
    assert prepared == request()
    assert prepared is not request()
    prompt = jobs.animations.prompt(prepared)
    assert "Return exactly one complete <!doctype html> document" in prompt
    assert "No network requests, imports, external assets" in prompt
    assert "window.ANIMATION_META" in prompt
    assert "window.renderFrame(t)" in prompt
    assert "1280x720, 30 fps, for 12 seconds" in prompt
    assert "canvas2d renderer" in prompt
    assert "must not require user input" in prompt
    assert prepared["concept"] in prompt
    assert "prefers-reduced-motion" in prompt
    interactive = jobs.animations.prompt(jobs.animations.prepare(request(interactive=True)))
    assert "may react to pointer or keyboard input" in interactive
    assert "must not require user input" not in interactive


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"title": ""}, "title"),
        ({"title": "x" * 121}, "title"),
        ({"concept": ""}, "concept"),
        ({"concept": "x" * 4001}, "concept"),
        ({"renderer": "webgl"}, "renderer"),
        ({"duration_seconds": 0}, "integer"),
        ({"duration_seconds": 181}, "integer"),
        ({"duration_seconds": True}, "integer"),
        ({"width": 63}, "integer"),
        ({"width": 1921}, "integer"),
        ({"height": 63}, "integer"),
        ({"height": 1921}, "integer"),
        ({"width": 1920, "height": 1920}, "pixels"),
        ({"fps": 0}, "integer"),
        ({"fps": 61}, "integer"),
        ({"interactive": 1}, "boolean"),
        ({"home": "/tmp/other"}, "fields"),
    ],
)
def test_prepare_rejects_unbounded_or_undeclared_inputs(jobs, patch, message):
    with pytest.raises(SketchError) as caught:
        jobs.animations.prepare(request(**patch))
    assert message.lower() in str(caught.value).lower() or message == "fields"
    assert jobs.list()["items"] == []
    assert jobs.sketches.artifacts.list() == []


@pytest.mark.parametrize(
    "response, message",
    [
        ("", "complete HTML"),
        ("<html></html>", "complete HTML"),
        ("<!doctype html><html></html>", "renderFrame"),
        (html().replace("ANIMATION_META", "META"), "runtime contract"),
        (html().replace("<script", "<template").replace("</script>", "</template>"), "runtime contract"),
        (html(body="fetch('/secret')"), "offline"),
        (html(body="new XMLHttpRequest()"), "offline"),
        (html(body="new WebSocket('ws://host')"), "offline"),
        (html(body="new EventSource('/events')"), "offline"),
        (html(body="ctx.drawImage(new Image(),0,0)").replace("</body>", '<img src="https://example.test/a.png"></body>'), "offline"),
        (html().replace("</head>", '<meta http-equiv="refresh" content="1; url=/x"></head>'), "offline"),
    ],
)
def test_extraction_rejects_incomplete_or_networked_documents(jobs, response, message):
    with pytest.raises(SketchError, match=message):
        jobs.animations.extract(response)
    assert jobs.sketches.artifacts.list() == []


def test_extracts_only_the_complete_document_from_provider_wrapping(jobs):
    document = html()
    assert jobs.animations.extract("Here is the file:\n```html\n" + document + "\n```\n") == document
    assert jobs.animations.extract(document) == document
    with pytest.raises(SketchError, match="2 MB"):
        jobs.animations.extract("x" * 2_000_001)


def test_publish_creates_exact_canonical_html_with_provenance(jobs):
    prepared = jobs.animations.prepare(request())
    document = html()
    result = jobs.animations.publish(prepared, "job-1", document)
    assert result["artifact_id"] == "code-animation-job-1"
    assert result["version"] == 1
    assert result["renderer"] == "canvas2d"
    assert result["frame"] == {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "duration_seconds": 12,
    }
    assert result["sha256"] == hashlib.sha256(document.encode()).hexdigest()
    artifact = jobs.sketches.artifacts.get(result["artifact_id"])
    assert artifact.kind == "widget"
    assert artifact.name == prepared["title"]
    assert artifact.content == document
    assert artifact.tags == ["animation", "canvas2d"]
    metadata = artifact.events[0].metadata
    assert metadata["media_job_id"] == "job-1"
    assert metadata["html_sha256"] == result["sha256"]
    assert metadata["provider_use_case"] == "reasoning"
    assert metadata["animation_request_sha256"] == hashlib.sha256(jobs.animations.prompt(prepared).encode()).hexdigest()


def test_publish_is_idempotent_and_fails_closed_on_identity_conflict(jobs):
    prepared = jobs.animations.prepare(request())
    first = jobs.animations.publish(prepared, "stable", html())
    assert jobs.animations.publish(prepared, "stable", html()) == first
    assert len(jobs.sketches.artifacts.list()) == 1
    with pytest.raises(SketchError, match="identity conflict"):
        jobs.animations.publish(prepared, "stable", html(body="ctx.fillRect(1,1,20,20)"))
    assert len(jobs.sketches.artifacts.list()) == 1
    artifact = jobs.sketches.artifacts.get(first["artifact_id"])
    assert artifact.content == html()
    assert artifact.version == 1


def test_durable_job_submission_replay_and_request_conflict(jobs):
    first = queued(jobs)
    replay = queued(jobs)
    assert replay["id"] == first["id"]
    assert first["operation"] == "code_animation_generate"
    assert first["input"] == request()
    assert first["status"] == "queued"
    reopened = MediaJobs(jobs.path, jobs.sketches)
    assert reopened.get(first["id"])["input"] == request()
    with pytest.raises(SketchError, match="conflicts"):
        queued(reopened, request(title="Different"))
    assert len(reopened.list()["items"]) == 1


def test_actual_worker_fails_honestly_when_no_reasoning_provider_is_configured(jobs):
    job = queued(jobs)
    MediaWorker(jobs).run_once(WorkerContext("gideon-media", "default", WorkerControl()))
    failed = jobs.get(job["id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == 1
    assert failed["result"] is None
    assert failed["error"]
    assert failed["events"][-1]["status"] == "failed"
    assert jobs.sketches.artifacts.list() == []


@pytest.mark.asyncio
async def test_http_and_native_tool_share_the_durable_job_store(jobs):
    app = web.Application()
    register_jobs(app, jobs)
    register_animations(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/capabilities/media/jobs",
            json={"operation": "code_animation_generate", "request_id": "http", "input": request()},
        )
        assert response.status == 202
        submitted = await response.json()
        assert submitted["input"]["renderer"] == "canvas2d"
        loaded = await client.get("/api/capabilities/media/jobs/" + submitted["id"])
        assert loaded.status == 200
        assert (await loaded.json())["id"] == submitted["id"]
        denied = await client.post(
            "/api/capabilities/media/jobs?home=other",
            json={"operation": "code_animation_generate", "request_id": "denied", "input": request()},
        )
        assert denied.status == 400
        pending_preview = await client.get("/api/capabilities/media/jobs/" + submitted["id"] + "/animation")
        assert pending_preview.status == 404

    tool = MediaToolProvider(jobs.sketches, MediaLibrary(jobs.sketches.artifacts))
    result = await tool.invoke(
        "media_code_animation_submit",
        {"request_id": "native", "input": request(renderer="svg", interactive=True)},
    )
    assert result.success
    native = json.loads(result.output)
    assert native["operation"] == "code_animation_generate"
    assert native["input"]["renderer"] == "svg"
    definitions = {item.name: item for item in await tool.list_tools()}
    definition = definitions["media_code_animation_submit"]
    assert definition.requires_approval is False
    assert definition.parameters["additionalProperties"] is False
    assert set(definition.parameters["required"]) == {"request_id", "input"}


@pytest.mark.asyncio
async def test_completed_http_preview_serves_exact_html_under_response_csp(jobs):
    job = queued(jobs, request_id="preview")
    claimed = jobs.claim()
    assert claimed["id"] == job["id"]
    document = html(body="window['fetch']('/would-run-without-csp')")
    result = jobs.animations.publish(job["input"], job["id"], document)
    jobs.finish(job["id"], result=result)
    app = web.Application()
    register_jobs(app, jobs)
    register_animations(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/capabilities/media/jobs/" + job["id"] + "/animation")
        assert response.status == 200
        assert await response.text() == document
        assert response.content_type == "text/html"
        assert response.headers["Content-Security-Policy"] == POLICY
        assert "connect-src 'none'" in POLICY
        assert "sandbox allow-scripts" in POLICY
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        denied = await client.get("/api/capabilities/media/jobs/" + job["id"] + "/animation?version=1")
        assert denied.status == 400


@pytest.mark.asyncio
async def test_preview_rejects_wrong_operation_and_missing_artifact(jobs):
    sketch = jobs.sketches.create({"width": 64, "height": 64, "request_id": "sketch"})
    other = jobs.submit({"operation": "sketch_export", "sketch_id": sketch["id"], "revision": 1, "request_id": "other"})
    app = web.Application()
    register_jobs(app, jobs)
    register_animations(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/capabilities/media/jobs/" + other["id"] + "/animation")
        assert response.status == 404
        missing = await client.get("/api/capabilities/media/jobs/missing/animation")
        assert missing.status == 404


def test_retry_preserves_same_animation_identity_after_provider_failure(jobs):
    job = queued(jobs)
    MediaWorker(jobs).run_once(WorkerContext("gideon-media", "default", WorkerControl()))
    failed = jobs.get(job["id"])
    retried = jobs.retry(job["id"], {"state_revision": failed["state_revision"]})
    assert retried["status"] == "queued"
    assert retried["attempt"] == 1
    assert retried["result"] is None
    assert retried["error"] is None
    assert retried["id"] == job["id"]
    assert retried["input"] == request()


def test_service_has_no_bundled_animation_template_or_fallback(jobs):
    source = AnimationService.execute.__code__.co_names
    assert "one_shot_completion" in source
    assert "publish" in source
    assert "template" not in source
    assert "fallback" not in source
    assert jobs.sketches.artifacts.list() == []
