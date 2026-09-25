import hashlib
import io
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw

from gideon.sdk.background import WorkerContext, WorkerControl
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def jobs(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    return MediaJobs(
        tmp_path / "jobs.sqlite3", SketchStore(tmp_path / "sketches.sqlite3", artifacts)
    )


def source(jobs, image=None):
    image = image or Image.new("RGBA", (8, 10), (80, 100, 120, 170))
    output = io.BytesIO()
    image.save(output, "PNG")
    artifact = jobs.sketches.artifacts.create_binary(
        name="Original image", data=output.getvalue(), mime="image/png"
    )
    return artifact.slug, output.getvalue()


def request(artifact_id, operations):
    return {
        "source_artifact_id": artifact_id,
        "source_version": 1,
        "operations": operations,
    }


def output_image(jobs, result):
    data, mime = jobs.sketches.artifacts.raw_bytes(result["artifact_id"])
    assert mime == "image/png"
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_ordered_cleanup_job_pixels_and_canonical_provenance_preserve_original(jobs):
    image = Image.new("RGBA", (8, 10))
    for y in range(10):
        for x in range(8):
            image.putpixel((x, y), (x * 20, y * 20, 40, 170))
    artifact_id, original = source(jobs, image)
    operations = [
        {"op": "crop", "x": 2, "y": 3, "width": 4, "height": 5},
        {"op": "flip", "axis": "horizontal"},
    ]
    body = {
        "operation": "image_cleanup",
        "request_id": "cleanup",
        "input": request(artifact_id, operations),
    }
    queued = jobs.submit(body)
    assert queued["input"]["output_width"] == 4
    assert queued["input"]["output_height"] == 5
    assert queued["input"]["engine"] == "pillow"
    assert jobs.submit(body) == queued
    MediaWorker(jobs).run_once(
        WorkerContext("gideon-media", "default", WorkerControl())
    )
    finished = jobs.get(queued["id"])
    assert finished["status"] == "succeeded"
    assert finished["attempt"] == 1
    rendered = output_image(jobs, finished["result"])
    assert rendered.size == (4, 5)
    assert rendered.getpixel((0, 0)) == (100, 60, 40, 170)
    assert rendered.getpixel((3, 4)) == (40, 140, 40, 170)
    assert jobs.sketches.artifacts.raw_bytes(artifact_id)[0] == original
    assert jobs.sketches.artifacts.get(artifact_id).version == 1
    metadata = (
        jobs.sketches.artifacts.get(finished["result"]["artifact_id"])
        .events[0]
        .metadata
    )
    assert metadata["engine"] == "Pillow"
    assert metadata["media_job_id"] == queued["id"]
    assert metadata["source_artifact_id"] == artifact_id
    assert metadata["source_version"] == 1
    assert (
        metadata["cleanup_request_sha256"]
        == hashlib.sha256(
            json.dumps(queued["input"], sort_keys=True).encode()
        ).hexdigest()
    )
    assert "model_selection" not in metadata
    assert "generation_request_sha256" not in metadata
    assert jobs.cleanup.execute(queued["input"], queued["id"]) == finished["result"]


def test_solid_edge_background_preserves_enclosed_matching_color_and_alpha(jobs):
    image = Image.new("RGBA", (9, 9), (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle((2, 2, 6, 6), outline=(0, 0, 0, 200), width=1)
    image.putpixel((4, 4), (255, 0, 0, 100))
    artifact_id, original = source(jobs, image)
    prepared = jobs.cleanup.prepare(
        request(
            artifact_id,
            [{"op": "solid_background", "color": "#ffffff", "tolerance": 0}],
        )
    )
    result = jobs.cleanup.execute(prepared, "solid-background")
    rendered = output_image(jobs, result)
    assert rendered.getpixel((0, 0))[3] == 0
    assert rendered.getpixel((8, 8))[3] == 0
    assert rendered.getpixel((3, 3)) == (255, 255, 255, 255)
    assert rendered.getpixel((2, 2)) == (0, 0, 0, 200)
    assert rendered.getpixel((4, 4)) == (255, 0, 0, 100)
    assert jobs.sketches.artifacts.raw_bytes(artifact_id)[0] == original


def test_resize_is_real_lanczos_and_rotation_is_counterclockwise(jobs):
    artifact_id, _ = source(jobs)
    prepared = jobs.cleanup.prepare(
        request(artifact_id, [{"op": "resize", "width": 4, "height": 5}])
    )
    rendered = output_image(jobs, jobs.cleanup.execute(prepared, "resize"))
    assert rendered.size == (4, 5)
    assert rendered.getpixel((1, 1))[3] == 170
    image = Image.new("RGBA", (2, 3), "blue")
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 255, 0, 255))
    rotated_id, _ = source(jobs, image)
    prepared = jobs.cleanup.prepare(
        request(rotated_id, [{"op": "rotate", "degrees": 90}])
    )
    rendered = output_image(jobs, jobs.cleanup.execute(prepared, "rotate"))
    assert rendered.size == (3, 2)
    assert rendered.getpixel((0, 0)) == (0, 255, 0, 255)
    assert rendered.getpixel((0, 1)) == (255, 0, 0, 255)
    assert prepared["output_width"] == 3
    assert prepared["output_height"] == 2


def test_brightness_and_contrast_preserve_source_alpha(jobs):
    artifact_id, _ = source(jobs)
    prepared = jobs.cleanup.prepare(
        request(artifact_id, [{"op": "brightness", "factor": 0.5}])
    )
    rendered = output_image(jobs, jobs.cleanup.execute(prepared, "brightness"))
    assert rendered.getpixel((2, 2)) == (40, 50, 60, 170)
    prepared = jobs.cleanup.prepare(
        request(artifact_id, [{"op": "contrast", "factor": 0}])
    )
    rendered = output_image(jobs, jobs.cleanup.execute(prepared, "contrast"))
    red, green, blue, alpha = rendered.getpixel((2, 2))
    assert red == green == blue
    assert alpha == 170
    assert 80 < red < 120


def test_sharpen_changes_real_edge_pixels_without_corrupting_transparency(jobs):
    image = Image.new("RGBA", (8, 8), (60, 60, 60, 120))
    ImageDraw.Draw(image).rectangle((4, 0, 7, 7), fill=(160, 160, 160, 120))
    artifact_id, _ = source(jobs, image)
    prepared = jobs.cleanup.prepare(
        request(
            artifact_id,
            [{"op": "sharpen", "radius": 1, "percent": 150, "threshold": 0}],
        )
    )
    rendered = output_image(jobs, jobs.cleanup.execute(prepared, "sharpen"))
    assert rendered.getpixel((3, 3))[0] < 60
    assert rendered.getpixel((4, 3))[0] > 160
    assert set(rendered.getchannel("A").getdata()) == {120}
    assert rendered.size == (8, 8)


def test_exif_orientation_is_applied_to_cleanup_coordinates(jobs):
    image = Image.new("RGB", (4, 6), "blue")
    exif = image.getexif()
    exif[274] = 6
    data = io.BytesIO()
    image.save(data, "JPEG", exif=exif)
    artifact = jobs.sketches.artifacts.create_binary(
        name="Camera original", data=data.getvalue(), mime="image/jpeg"
    )
    prepared = jobs.cleanup.prepare(
        request(
            artifact.slug, [{"op": "crop", "x": 4, "y": 0, "width": 2, "height": 4}]
        )
    )
    rendered = output_image(jobs, jobs.cleanup.execute(prepared, "orientation"))
    assert rendered.size == (2, 4)
    assert rendered.getpixel((1, 1))[2] > 240
    assert jobs.sketches.artifacts.raw_bytes(artifact.slug)[0] == data.getvalue()


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "crop", "x": 7, "y": 0, "width": 2, "height": 1},
        {"op": "resize", "width": 4097, "height": 1},
        {"op": "resize", "width": True, "height": 1},
        {"op": "rotate", "degrees": 45},
        {"op": "flip", "axis": "diagonal"},
        {"op": "brightness", "factor": float("nan")},
        {"op": "contrast", "factor": 4},
        {"op": "sharpen", "radius": 9, "percent": 10, "threshold": 1},
        {"op": "solid_background", "color": "white", "tolerance": 0},
        {"op": "solid_background", "color": "#ffffff", "tolerance": 101},
        {"op": "semantic_remove_person"},
        {"op": "resize", "width": 4, "height": 5, "home": "/tmp"},
    ],
)
def test_invalid_or_unimplemented_transforms_never_queue(jobs, operation):
    artifact_id, _ = source(jobs)
    with pytest.raises(SketchError):
        jobs.submit(
            {
                "operation": "image_cleanup",
                "request_id": "invalid",
                "input": request(artifact_id, [operation]),
            }
        )
    assert jobs.list()["items"] == []


def test_pipeline_bounds_are_checked_after_prior_transforms(jobs):
    artifact_id, _ = source(jobs)
    with pytest.raises(SketchError, match="Crop"):
        jobs.cleanup.prepare(
            request(
                artifact_id,
                [
                    {"op": "resize", "width": 2, "height": 2},
                    {"op": "crop", "x": 0, "y": 0, "width": 3, "height": 1},
                ],
            )
        )
    with pytest.raises(SketchError, match="megapixels"):
        jobs.cleanup.prepare(
            request(
                artifact_id,
                [
                    {"op": "resize", "width": 4096, "height": 4096},
                    {"op": "solid_background", "color": "#ffffff", "tolerance": 0},
                ],
            )
        )
    for invalid in (None, "", [], {}):
        with pytest.raises(SketchError):
            jobs.cleanup.prepare(request(invalid, [{"op": "rotate", "degrees": 90}]))
    for operations in ([], [{"op": "rotate", "degrees": 90}] * 11):
        with pytest.raises(SketchError):
            jobs.cleanup.prepare(request(artifact_id, operations))


@pytest.mark.asyncio
async def test_http_and_native_tool_queue_same_cleanup_contract(jobs):
    artifact_id, original = source(jobs)
    app = web.Application()
    register_jobs(app, jobs)
    async with TestClient(TestServer(app)) as client:
        payload = request(artifact_id, [{"op": "flip", "axis": "vertical"}])
        response = await client.post(
            "/api/capabilities/media/jobs",
            json={"operation": "image_cleanup", "request_id": "http", "input": payload},
        )
        assert response.status == 202
        queued = await response.json()
        assert queued["operation"] == "image_cleanup"
        assert queued["input"]["source_version"] == 1
        provider = MediaToolProvider(
            jobs.sketches, MediaLibrary(jobs.sketches.artifacts)
        )
        result = await provider.invoke(
            "media_cleanup_submit", {"request_id": "tool", "input": payload}
        )
        assert result.success
        assert json.loads(result.output)["input"] == queued["input"]
        result = await provider.invoke(
            "media_cleanup_submit",
            {"request_id": "escape", "input": dict(payload, home="other")},
        )
        assert not result.success
        assert len(jobs.list()["items"]) == 2
        assert jobs.sketches.artifacts.raw_bytes(artifact_id)[0] == original
