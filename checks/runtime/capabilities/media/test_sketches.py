import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.interfaces.dashboard.handlers.capabilities_media import STORE_KEY, register
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore


@pytest.fixture
def store(tmp_path):
    return SketchStore(
        tmp_path / "sketches.sqlite3", NativeArtifactProvider(tmp_path / "artifacts")
    )


def image_bytes(color="blue", size=(64, 48)):
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def drawing(tool="draw", color="#ff0000", width=8, points=None):
    return dict(
        tool=tool, color=color, width=width, points=points or [[8, 20], [40, 20]]
    )


def blank(store, request_id="first", **extra):
    return store.create(dict(width=64, height=48, request_id=request_id, **extra))


def pixels(store, result):
    raw, mime = store.artifacts.raw_bytes(
        result["artifact_id"], version=result["version"]
    )
    assert mime == "image/png"
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def test_original_version_and_overlay_survive_reopen(store):
    original = image_bytes()
    source = store.artifacts.create_binary(
        name="Original", data=original, mime="image/png"
    )
    sketch = blank(store, source_artifact_id=source.slug, source_version=1)
    changed = store.update(sketch["id"], {"revision": 1, "strokes": [drawing()]})
    assert changed["revision"] == 2
    assert changed["updated_at"] >= sketch["updated_at"]
    reopened = SketchStore(store.path, NativeArtifactProvider(store.artifacts.root))
    assert reopened.get(sketch["id"]) == changed
    result = reopened.export(sketch["id"], {"revision": 2})
    image = pixels(reopened, result)
    assert image.size == (64, 48)
    assert image.getpixel((20, 20)) == (255, 0, 0, 255)
    assert image.getpixel((60, 40)) == (0, 0, 255, 255)
    assert reopened.artifacts.raw_bytes(source.slug, version=1)[0] == original
    assert reopened.artifacts.list_versions(source.slug) == [1]
    assert result["source_artifact_id"] == source.slug
    assert result["source_version"] == 1
    artifact = reopened.artifacts.get(result["artifact_id"])
    assert artifact.events[0].metadata["sketch_id"] == sketch["id"]


def test_erase_reveals_original_and_later_draw_remains(store):
    source = store.artifacts.create_binary(
        name="Blue", data=image_bytes(), mime="image/png"
    )
    sketch = blank(
        store,
        source_artifact_id=source.slug,
        source_version=1,
        strokes=[
            drawing(),
            drawing("erase", width=4, points=[[20, 20]]),
            drawing(color="#00ff00", width=2, points=[[30, 20]]),
        ],
    )
    result = store.export(sketch["id"], {"revision": 1})
    image = pixels(store, result)
    assert image.getpixel((20, 20)) == (0, 0, 255, 255)
    assert image.getpixel((30, 20)) == (0, 255, 0, 255)
    assert image.getpixel((10, 20)) == (255, 0, 0, 255)


def test_blank_export_and_undo_by_saved_stroke_history(store):
    sketch = blank(store, strokes=[drawing()])
    first = store.export(sketch["id"], {"revision": 1})
    assert pixels(store, first).getpixel((20, 20)) == (255, 0, 0, 255)
    updated = store.update(sketch["id"], {"revision": 1, "strokes": []})
    second = store.export(sketch["id"], {"revision": updated["revision"]})
    assert second["artifact_id"] != first["artifact_id"]
    assert pixels(store, second).getpixel((20, 20)) == (255, 255, 255, 255)
    assert pixels(store, first).getpixel((20, 20)) == (255, 0, 0, 255)
    assert second["source_artifact_id"] is None
    assert second["source_version"] is None


def test_create_replay_conflict_and_export_replay(store):
    sketch = blank(store)
    assert blank(store) == sketch
    with pytest.raises(SketchError) as conflict:
        blank(store, strokes=[drawing()])
    assert conflict.value.status == 409
    first = store.export(sketch["id"], {"revision": 1})
    assert store.export(sketch["id"], {"revision": 1}) == first
    assert store.artifacts.list_versions(first["artifact_id"]) == [1]
    assert len(store.list()) == 1


def test_concurrent_updates_have_one_winner(store):
    sketch = blank(store)

    def save(color):
        try:
            return store.update(
                sketch["id"], {"revision": 1, "strokes": [drawing(color=color)]}
            )
        except SketchError as exc:
            return exc.status

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(save, ["#00ff00", "#ff0000"]))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert 409 in outcomes
    assert store.get(sketch["id"])["revision"] == 2
    with pytest.raises(SketchError) as conflict:
        store.export(sketch["id"], {"revision": 1})
    assert conflict.value.status == 409


@pytest.mark.parametrize(
    "patch",
    [
        {"width": 0},
        {"width": 4097},
        {"height": -1},
        {"height": True},
        {"width": 1.5},
        {"request_id": "../escape"},
        {"source_version": 1},
        {"source_artifact_id": "../escape", "source_version": 1},
        {"source_artifact_id": "missing"},
        {"provider": "other"},
        {"home": "/tmp/other"},
    ],
)
def test_invalid_creation_does_not_persist(store, patch):
    body = dict(width=64, height=48, request_id="request")
    body.update(patch)
    with pytest.raises(SketchError):
        store.create(body)
    assert store.list() == []


@pytest.mark.parametrize(
    "stroke",
    [
        drawing(tool="fill"),
        drawing(color="red"),
        drawing(color="#fff"),
        drawing(color="#ff000000"),
        drawing(width=0),
        drawing(width=129),
        drawing(width=True),
        drawing(points=[[64, 0]]),
        drawing(points=[[0, 48]]),
        drawing(points=[[-1, 0]]),
        drawing(points=[[float("nan"), 0]]),
        drawing(points=[[float("inf"), 0]]),
        drawing(points=[[True, 0]]),
        drawing(points=[[1]]),
        drawing(points=[[1, 2, 3]]),
        drawing(points=["bad"]),
        {**drawing(), "points": []},
        {**drawing(), "secret": "bad"},
    ],
)
def test_invalid_strokes_reject_without_corrupting_saved_state(store, stroke):
    sketch = blank(store)
    with pytest.raises(SketchError):
        store.update(sketch["id"], {"revision": 1, "strokes": [stroke]})
    assert store.get(sketch["id"]) == sketch


def test_bounds_for_strokes_points_and_source_dimensions(store):
    with pytest.raises(SketchError):
        blank(store, strokes=[drawing()] * 501)
    with pytest.raises(SketchError):
        blank(store, strokes=[drawing(points=[[1, 1]] * 5001)])
    with pytest.raises(SketchError):
        blank(store, strokes=[drawing(points=[[1, 1]] * 5000)] * 5)
    source = store.artifacts.create_binary(
        name="Different", data=image_bytes(size=(2, 2)), mime="image/png"
    )
    with pytest.raises(SketchError, match="dimensions"):
        blank(store, source_artifact_id=source.slug, source_version=1)
    assert store.list() == []


def test_source_pinned_even_after_new_artifact_version(store):
    source = store.artifacts.create_binary(
        name="Pinned", data=image_bytes(), mime="image/png"
    )
    sketch = blank(store, source_artifact_id=source.slug, source_version=1)
    store.artifacts.update_binary(
        source.slug, data=image_bytes("yellow"), mime="image/png"
    )
    assert store.source(sketch)[0] == image_bytes()
    result = store.export(sketch["id"], {"revision": 1})
    assert pixels(store, result).getpixel((0, 0)) == (0, 0, 255, 255)
    assert store.artifacts.get(source.slug).version == 2


def test_missing_wrong_kind_and_corrupt_sources(store):
    with pytest.raises(SketchError) as missing:
        blank(store, source_artifact_id="absent", source_version=1)
    assert missing.value.status == 404
    document = store.artifacts.create(name="Document", content="text", kind="text")
    with pytest.raises(SketchError):
        blank(store, source_artifact_id=document.slug, source_version=1)
    corrupt = store.artifacts.create_binary(
        name="Corrupt", data=b"not an image", mime="image/png"
    )
    with pytest.raises(SketchError, match="supported image"):
        blank(store, source_artifact_id=corrupt.slug, source_version=1)
    assert store.list() == []


def test_two_homes_cannot_resolve_other_sources(store, tmp_path):
    other = SketchStore(
        tmp_path / "other" / "sketches.sqlite3",
        NativeArtifactProvider(tmp_path / "other" / "artifacts"),
    )
    source = store.artifacts.create_binary(
        name="Private", data=image_bytes(), mime="image/png"
    )
    with pytest.raises(SketchError) as missing:
        blank(other, source_artifact_id=source.slug, source_version=1)
    assert missing.value.status == 404
    sketch = blank(store)
    with pytest.raises(SketchError):
        other.get(sketch["id"])
    assert other.list() == []


def test_export_collision_never_returns_unrelated_bytes(store):
    sketch = blank(store)
    slug = f"sketch-{sketch['id']}-r1"
    intruder = store.artifacts.create_binary(
        name="Unrelated", slug=slug, data=image_bytes("black"), mime="image/png"
    )
    with pytest.raises(SketchError) as conflict:
        store.export(sketch["id"], {"revision": 1})
    assert conflict.value.status == 409
    assert store.artifacts.raw_bytes(intruder.slug)[0] == image_bytes("black")


def test_transparent_source_alpha_and_canvas_edges(store):
    raw = image_bytes((0, 0, 0, 0))
    source = store.artifacts.create_binary(
        name="Transparent", data=raw, mime="image/png"
    )
    sketch = blank(
        store,
        source_artifact_id=source.slug,
        source_version=1,
        strokes=[drawing(width=2, points=[[0, 0], [63, 0]])],
    )
    result = store.export(sketch["id"], {"revision": 1})
    image = pixels(store, result)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert image.getpixel((63, 0)) == (255, 0, 0, 255)
    assert image.getpixel((20, 40)) == (0, 0, 0, 0)
    assert store.artifacts.raw_bytes(source.slug)[0] == raw


def test_source_deleted_after_save_is_an_explicit_export_failure(store):
    source = store.artifacts.create_binary(
        name="Temporary", data=image_bytes(), mime="image/png"
    )
    sketch = blank(store, source_artifact_id=source.slug, source_version=1)
    store.artifacts.delete(source.slug)
    with pytest.raises(SketchError) as missing:
        store.export(sketch["id"], {"revision": 1})
    assert missing.value.status == 404
    assert store.get(sketch["id"]) == sketch
    assert store.artifacts.get(f"sketch-{sketch['id']}-r1") is None


def test_source_exif_orientation_is_preserved_in_export(store):
    image = Image.new("RGB", (48, 64), "blue")
    image.putpixel((0, 0), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, "PNG", exif=exif)
    original = output.getvalue()
    source = store.artifacts.create_binary(
        name="Rotated", data=original, mime="image/png"
    )
    sketch = blank(store, source_artifact_id=source.slug, source_version=1)
    result = store.export(sketch["id"], {"revision": 1})
    flattened = pixels(store, result)
    assert flattened.size == (64, 48)
    assert flattened.getpixel((63, 0)) == (255, 0, 0, 255)
    assert flattened.getpixel((0, 47)) == (0, 0, 255, 255)
    assert store.artifacts.raw_bytes(source.slug)[0] == original


def test_unknown_detail_and_invalid_update_envelopes(store):
    with pytest.raises(SketchError) as missing:
        store.update("absent", {"revision": 1, "strokes": []})
    assert missing.value.status == 404
    sketch = blank(store)
    for body in (
        {},
        {"strokes": []},
        {"revision": True, "strokes": []},
        {"revision": 1, "strokes": "bad"},
    ):
        with pytest.raises(SketchError):
            store.update(sketch["id"], body)
    assert store.get(sketch["id"]) == sketch


@pytest.mark.asyncio
async def test_real_http_create_save_reload_export(store):
    app = web.Application(client_max_size=1024 * 1024)
    app[STORE_KEY] = store
    register(app)
    async with TestClient(TestServer(app)) as client:
        prefix = "/api/capabilities/media/sketches"
        response = await client.get(prefix)
        assert response.status == 200
        assert await response.json() == {"items": []}
        response = await client.post(
            prefix, json={"width": 64, "height": 48, "request_id": "http"}
        )
        assert response.status == 201
        sketch = await response.json()
        detail = prefix + "/" + sketch["id"]
        response = await client.put(
            detail, json={"revision": 1, "strokes": [drawing()]}
        )
        assert response.status == 200
        saved = await response.json()
        response = await client.get(detail)
        assert await response.json() == saved
        response = await client.post(detail + "/export", json={"revision": 2})
        assert response.status == 200
        result = await response.json()
        assert pixels(store, result).getpixel((20, 20)) == (255, 0, 0, 255)
        response = await client.get(prefix)
        assert (await response.json())["items"] == [saved]
        response = await client.put(detail, json={"revision": 1, "strokes": []})
        assert response.status == 409
        response = await client.get(detail + "/source")
        assert response.status == 404


@pytest.mark.asyncio
async def test_real_http_source_and_invalid_requests(store):
    source = store.artifacts.create_binary(
        name="HTTP source", data=image_bytes(), mime="image/png"
    )
    sketch = blank(store, source_artifact_id=source.slug, source_version=1)
    app = web.Application(client_max_size=1024 * 1024)
    app[STORE_KEY] = store
    register(app)
    async with TestClient(TestServer(app)) as client:
        prefix = "/api/capabilities/media/sketches"
        detail = prefix + "/" + sketch["id"]
        response = await client.get(detail + "/source")
        assert response.status == 200
        assert response.content_type == "image/png"
        assert await response.read() == image_bytes()
        response = await client.post(
            prefix, data="invalid", headers={"Content-Type": "application/json"}
        )
        assert response.status == 400
        response = await client.post(prefix, json=[])
        assert response.status == 400
        response = await client.get(prefix + "?home=/tmp/other")
        assert response.status == 400
        response = await client.put(
            detail,
            json={"revision": 1, "strokes": [], "source_artifact_id": source.slug},
        )
        assert response.status == 400
        response = await client.post(
            detail + "/export", json={"revision": 1, "account": "elsewhere"}
        )
        assert response.status == 400
        response = await client.post(prefix, data="x" * (1024 * 1024 + 1))
        assert response.status == 413
        response = await client.get(prefix + "/does-not-exist")
        assert response.status == 404
        assert store.get(sketch["id"])["revision"] == 1
