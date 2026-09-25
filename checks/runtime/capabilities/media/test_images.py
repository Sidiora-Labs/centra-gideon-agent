import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider
from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.sdk.image import ImageControl, ImageGenModel, ImageResult
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.images import ImageService
from gideon.workspace.capabilities.media.images_http import register_images
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def service(tmp_path):
    provider = OpenAIImageProvider(provider_name="unconfigured-image", api_key="")
    return ImageService(
        NativeArtifactProvider(tmp_path / "artifacts"),
        selector=lambda: (provider, "image"),
    )


def image(service, size=(12, 10), color=(10, 20, 30, 255)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, "PNG")
    return (
        service.artifacts.create_binary(
            name="Original", data=output.getvalue(), mime="image/png"
        ),
        output.getvalue(),
    )


def request(service, **values):
    return service.prepare(dict(prompt="Draw a mountain", **values))


def test_request_pins_real_source_and_preserves_alpha_and_original(service):
    original, raw = image(service)
    mask, mask_raw = image(service, color=(255, 255, 255, 0))
    prepared = request(
        service,
        source_artifact_id=original.slug,
        source_version=1,
        mask_artifact_id=mask.slug,
        mask_version=1,
        controls={"seed": 42},
    )
    assert prepared["selection"] == "unconfigured-image:image"
    assert prepared["source_artifact_id"] == original.slug
    assert prepared["source_version"] == 1
    assert prepared["mask_artifact_id"] == mask.slug
    assert prepared["mask_version"] == 1
    assert prepared["controls"] == {"seed": 42}
    assert service.source(original.slug, 1).size == (12, 10)
    assert service.source(original.slug, 1).getpixel((0, 0)) == (10, 20, 30, 255)
    assert service.source(mask.slug, 1).getpixel((0, 0))[3] == 0
    assert service.artifacts.raw_bytes(original.slug)[0] == raw
    assert service.artifacts.raw_bytes(mask.slug)[0] == mask_raw
    assert prepared["size"] == ""
    assert prepared["prompt"] == "Draw a mountain"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"prompt": ""},
        {"prompt": " "},
        {"prompt": 1},
        {"prompt": "a" * 4001},
        {"prompt": "a", "provider": "other"},
        {"prompt": "a", "home": "/tmp"},
        {"prompt": "a", "controls": {"unknown": 1}},
        {"prompt": "a", "controls": {"seed": True}},
        {"prompt": "a", "controls": {"seed": 1.5}},
        {"prompt": "a", "controls": {"steps": -1}},
        {"prompt": "a", "controls": {"guidance": float("nan")}},
        {"prompt": "a", "controls": {"strength": float("inf")}},
        {"prompt": "a", "controls": []},
        {"prompt": "a", "source_artifact_id": "only-id"},
        {"prompt": "a", "source_version": 1},
        {"prompt": "a", "mask_artifact_id": "mask", "mask_version": 1},
        {"prompt": "a", "size": []},
    ],
)
def test_malformed_requests_fail_before_any_provider_generation(service, body):
    with pytest.raises(SketchError):
        service.prepare(body)
    assert service.artifacts.list() == []


def test_mismatched_mask_missing_pinned_version_and_nonimage_fail(service):
    source, raw = image(service)
    mask, _ = image(service, size=(4, 4))
    with pytest.raises(SketchError):
        request(
            service,
            source_artifact_id=source.slug,
            source_version=1,
            mask_artifact_id=mask.slug,
            mask_version=1,
        )
    with pytest.raises(SketchError):
        request(service, source_artifact_id=source.slug, source_version=5)
    with pytest.raises(SketchError):
        request(service, source_artifact_id="missing", source_version=1)
    assert service.artifacts.raw_bytes(source.slug)[0] == raw
    with pytest.raises(SketchError):
        service.source(source.slug, True)


def test_provider_control_contract_defaults_are_backwards_compatible():
    model = ImageGenModel("legacy", "description", ["12x10"], True, True, False)
    assert model.supported_controls == {}
    assert model.supports_mask is False
    assert model.supports_edit is True
    assert model.sizes == ["12x10"]
    control = ImageControl(1, 50, True)
    assert control.minimum == 1
    assert control.maximum == 50
    assert control.integer is True
    assert ImageControl(0, 1).integer is False


def test_typed_advertised_controls_are_required_and_enforced(service):
    prepared = request(
        service,
        controls={"seed": 42, "steps": 20, "guidance": 7.5, "strength": 0.6},
        size="12x10",
    )
    model = ImageGenModel(
        "image",
        sizes=["12x10"],
        supported_controls={
            "seed": ImageControl(0, 100, True),
            "steps": ImageControl(1, 30, True),
            "guidance": ImageControl(0, 10),
            "strength": ImageControl(0, 1),
        },
    )
    service.validate_model(prepared, model)
    for key in prepared["controls"]:
        unsupported = ImageGenModel(
            "image",
            sizes=["12x10"],
            supported_controls={
                name: control
                for name, control in model.supported_controls.items()
                if name != key
            },
        )
        with pytest.raises(SketchError, match=key):
            service.validate_model(prepared, unsupported)
    for key, value in [
        ("seed", 101),
        ("steps", 0),
        ("guidance", 11),
        ("strength", 1.5),
    ]:
        with pytest.raises(SketchError, match=key):
            service.validate_model(dict(prepared, controls={key: value}), model)
    with pytest.raises(SketchError, match="size"):
        service.validate_model(dict(prepared, size="99x99"), model)


def test_conditioning_and_mask_are_separate_advertised_capabilities(service):
    source, _ = image(service)
    mask, _ = image(service, color=(0, 0, 0, 0))
    prepared = request(
        service,
        source_artifact_id=source.slug,
        source_version=1,
        mask_artifact_id=mask.slug,
        mask_version=1,
    )
    with pytest.raises(SketchError, match="conditioning"):
        service.validate_model(prepared, ImageGenModel("image"))
    with pytest.raises(SketchError, match="mask"):
        service.validate_model(prepared, ImageGenModel("image", supports_edit=True))
    service.validate_model(
        prepared, ImageGenModel("image", supports_edit=True, supports_mask=True)
    )
    assert service.source(mask.slug, 1).getpixel((5, 5))[3] == 0


def test_real_typed_image_result_materializes_png_and_retains_provenance(service):
    source, source_raw = image(service)
    prepared = request(service, source_artifact_id=source.slug, source_version=1)
    output = io.BytesIO()
    Image.new("RGB", (7, 9), "orange").save(output, "JPEG")
    result = ImageResult(
        b64=base64.b64encode(output.getvalue()).decode(), mime="image/jpeg"
    )
    saved = service.materialize(result, prepared, "actual-image-result")
    assert saved["artifact_id"] == "image-job-actual-image-result"
    assert saved["version"] == 1
    data, mime = service.artifacts.raw_bytes(saved["artifact_id"])
    assert mime == "image/png"
    actual = Image.open(io.BytesIO(data))
    assert actual.size == (7, 9)
    assert actual.mode == "RGBA"
    assert actual.getpixel((3, 3))[0] > 240
    assert service.artifacts.raw_bytes(source.slug)[0] == source_raw
    artifact = service.artifacts.get(saved["artifact_id"])
    assert (
        artifact.events[0].metadata["generation_request_sha256"]
        == hashlib.sha256(json.dumps(prepared, sort_keys=True).encode()).hexdigest()
    )
    assert artifact.events[0].metadata["source_artifact_id"] == source.slug
    assert artifact.events[0].metadata["source_version"] == 1
    assert artifact.events[0].metadata["model_selection"] == prepared["selection"]
    assert artifact.events[0].metadata["media_job_id"] == "actual-image-result"
    assert service.materialize(result, prepared, "actual-image-result") == saved
    assert len(service.artifacts.list()) == 2
    with pytest.raises(SketchError, match="conflicts"):
        service.materialize(
            result, dict(prepared, prompt="Changed"), "actual-image-result"
        )


def test_invalid_or_absent_image_results_never_create_artifact(service):
    prepared = request(service)
    for result in (
        ImageResult(),
        ImageResult(b64="notbase64"),
        ImageResult(b64=base64.b64encode(b"not an image").decode()),
    ):
        with pytest.raises(SketchError):
            service.materialize(result, prepared, "invalid")
    assert service.artifacts.list() == []


@pytest.mark.asyncio
async def test_actual_unconfigured_adapter_execution_is_honestly_unavailable(service):
    prepared = request(service)
    with pytest.raises(SketchError) as unavailable:
        await service.execute(prepared, "no-credentials")
    assert unavailable.value.status == 503
    assert service.artifacts.list() == []
    with pytest.raises(SketchError) as mismatch:
        await service.execute(dict(prepared, selection="different:model"), "changed")
    assert mismatch.value.status == 409


def test_actual_worker_failed_image_job_reopens_and_retries_without_output(
    service, tmp_path
):
    sketches = SketchStore(tmp_path / "sketches.sqlite3", service.artifacts)
    jobs = MediaJobs(tmp_path / "jobs.sqlite3", sketches, images=service)
    body = dict(
        operation="image_generate",
        request_id="queued-image",
        input={"prompt": "A real generation request"},
    )
    queued = jobs.submit(body)
    assert queued["operation"] == "image_generate"
    assert queued["input"]["selection"] == "unconfigured-image:image"
    assert queued["status"] == "queued"
    assert jobs.submit(body) == queued
    with pytest.raises(SketchError):
        jobs.submit(dict(body, input={"prompt": "Changed request"}))
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    failed = jobs.get(queued["id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == 1
    assert failed["result"] is None
    assert "unavailable" in failed["error"]
    assert failed["input"] == queued["input"]
    reopened = MediaJobs(jobs.path, sketches, images=service)
    assert reopened.get(queued["id"]) == failed
    retry = reopened.retry(queued["id"], {"state_revision": failed["state_revision"]})
    assert retry["status"] == "queued"
    assert retry["events"][-2]["error"] == failed["error"]
    assert service.artifacts.list() == []


@pytest.mark.asyncio
async def test_http_and_native_tools_queue_same_typed_requests(service, tmp_path):
    sketches = SketchStore(tmp_path / "sketches.sqlite3", service.artifacts)
    jobs = MediaJobs(tmp_path / "jobs.sqlite3", sketches, images=service)
    app = web.Application()
    register_jobs(app, jobs)
    register_images(app)
    async with TestClient(TestServer(app)) as client:
        body = dict(
            operation="image_generate",
            request_id="http",
            input={"prompt": "Image from API"},
        )
        response = await client.post("/api/capabilities/media/images", json=body)
        assert response.status == 202
        queued = await response.json()
        assert queued["input"]["prompt"] == "Image from API"
        assert queued["operation"] == "image_generate"
        assert queued["status"] == "queued"
        response = await client.get("/api/capabilities/media/jobs/" + queued["id"])
        assert await response.json() == queued
        response = await client.post(
            "/api/capabilities/media/images?home=other", json=body
        )
        assert response.status == 400
        response = await client.post(
            "/api/capabilities/media/images", json=dict(body, runtime="other")
        )
        assert response.status == 400
        provider = MediaToolProvider(sketches, MediaLibrary(service.artifacts))
        provider.jobs = jobs
        result = await provider.invoke(
            "media_image_submit",
            {"request_id": "tool", "input": {"prompt": "Image from tool"}},
        )
        assert result.success
        assert json.loads(result.output)["input"]["prompt"] == "Image from tool"
        result = await provider.invoke(
            "media_image_submit",
            {"request_id": "escape", "input": {"prompt": "No", "source_path": "/tmp"}},
        )
        assert not result.success
        assert len(jobs.list()["items"]) == 2
