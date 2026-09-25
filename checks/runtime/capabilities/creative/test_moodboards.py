import asyncio
import copy
from io import BytesIO
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.artifacts import registry
from gideon.workspace.artifacts.handlers import api_artifact_raw
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.creative.moodboards import BoardStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore

BASE = "/api/capabilities/creative/boards"


def image_source(home, name="Palette study"):
    buffer = BytesIO()
    Image.new("RGB", (3, 2), "#aabbcc").save(buffer, format="PNG")
    provider = NativeArtifactProvider(home / "artifacts")
    artifact = provider.create_binary(
        name=name, data=buffer.getvalue(), mime="image/png"
    )
    return provider, artifact, buffer.getvalue()


def payload(source, **changes):
    return {
        "title": "Atmosphere",
        "request_id": str(uuid4()),
        "ingredient_ids": [],
        "groups": [
            {
                "id": "group-a",
                "title": "Light",
                "cards": [
                    {
                        "id": "card-a",
                        "artifact_id": source.slug,
                        "artifact_version": source.version,
                        "caption": "Cool shadows",
                        "colors": ["#AABBCC"],
                    }
                ],
            }
        ],
        **changes,
    }


def test_order_captions_colors_provenance_and_restore(tmp_path):
    provider, source, data = image_source(tmp_path)
    store = BoardStore(tmp_path)
    first = store.create(payload(source))
    assert first["revision"] == 1
    assert first["groups"][0]["cards"][0]["colors"] == ["#aabbcc"]
    card = first["groups"][0]["cards"][0]
    assert card["provenance"]["title"] == source.name
    assert card["provenance"]["kind"] == "image"
    assert card["provenance"]["added_at"]
    patch = store.editable(first)
    patch["groups"].append({"id": "group-b", "title": "Texture", "cards": []})
    patch["groups"].reverse()
    patch["groups"][1]["cards"][0]["caption"] = "New caption"
    second = store.update(first["id"], {**patch, "revision": 1})
    assert [g["id"] for g in second["groups"]] == ["group-b", "group-a"]
    assert second["groups"][1]["cards"][0]["caption"] == "New caption"
    assert second["groups"][1]["cards"][0]["provenance"] == card["provenance"]
    assert second["created_at"] == first["created_at"]
    restored = store.restore(first["id"], {"revision": 2, "target_revision": 1})
    assert restored["groups"] == first["groups"]
    assert restored["revision"] == 3
    assert provider.raw_bytes(source.slug, version=1)[0] == data
    reopened = BoardStore(tmp_path)
    assert reopened.export(first["id"]) == restored
    assert reopened.export(first["id"], 2) == second
    assert reopened.revisions(first["id"]) == [restored, second, first]


def test_card_source_pin_survives_removal_and_cannot_be_rebound(tmp_path):
    _, source, _ = image_source(tmp_path)
    _, other, _ = image_source(tmp_path, "Other source")
    store = BoardStore(tmp_path)
    first = store.create(payload(source))
    store.update(first["id"], {"revision": 1, "groups": []})
    rebound = store.editable(first)
    rebound["groups"][0]["cards"][0]["artifact_id"] = other.slug
    with pytest.raises(CatalogError, match="immutable"):
        store.update(first["id"], {**rebound, "revision": 2})
    assert store.export(first["id"])["groups"] == []
    restored = store.restore(first["id"], {"revision": 2, "target_revision": 1})
    assert (
        restored["groups"][0]["cards"][0]["provenance"]
        == first["groups"][0]["cards"][0]["provenance"]
    )
    assert len(store.revisions(first["id"])) == 3


def test_real_source_version_missing_and_two_homes(tmp_path):
    home = tmp_path / "one"
    provider, source, data = image_source(home)
    store = BoardStore(home)
    first = store.create(payload(source))
    status = store.get(first["id"])["source_status"][0]
    assert status["missing"] is False
    assert status["preview_url"] == f"/api/artifacts/{source.slug}/raw?version=1"
    other = BoardStore(tmp_path / "two")
    assert other.sources()["items"] == []
    with pytest.raises(CatalogError) as error:
        other.create(payload(source))
    assert error.value.status == 404
    assert other.list()["total"] == 0
    provider.delete(source.slug)
    missing = store.get(first["id"])["source_status"][0]
    assert missing == {"card_id": "card-a", "missing": True, "preview_url": None}
    edited = store.update(first["id"], {"revision": 1, "title": "Retained inspiration"})
    assert edited["groups"] == first["groups"]
    assert store.export(first["id"], 1)["title"] == "Atmosphere"
    assert data.startswith(b"\x89PNG")


def test_catalog_links_and_idempotent_creates(tmp_path):
    _, source, _ = image_source(tmp_path)
    catalog = IngredientStore(tmp_path)
    ingredient = catalog.create(
        {"request_id": "ingredient-1", "type": "place", "title": "City"}
    )
    store = BoardStore(tmp_path)
    request = payload(source, ingredient_ids=[ingredient["id"], ingredient["id"]])
    first = store.create(request)
    assert first["ingredient_ids"] == [ingredient["id"]]
    assert store.get(first["id"])["ingredient_status"] == [
        {"id": ingredient["id"], "missing": False}
    ]
    assert store.create(request) == first
    with pytest.raises(CatalogError) as error:
        store.create({**request, "title": "Different"})
    assert error.value.status == 409
    with pytest.raises(CatalogError) as error:
        store.create(payload(source, ingredient_ids=["missing-ingredient"]))
    assert error.value.status == 404
    assert store.list()["total"] == 1
    assert catalog.get(ingredient["id"])["revision"] == 1


def test_search_pagination_and_source_picker(tmp_path):
    _, source, _ = image_source(tmp_path)
    store = BoardStore(tmp_path)
    for n in range(5):
        store.create(payload(source, title=f"Blue board {n}"))
    store.create(payload(source, title="Red board"))
    assert store.list(q="BLUE")["total"] == 5
    pages = [store.list(q="blue", limit=2, offset=n)["items"] for n in (0, 2, 4)]
    assert [len(page) for page in pages] == [2, 2, 1]
    assert len({item["id"] for page in pages for item in page}) == 5
    assert store.list(q="empty")["items"] == []
    sources = store.sources("Palette")["items"]
    assert sources == [
        {"id": source.slug, "title": source.name, "kind": "image", "version": 1}
    ]
    assert store.sources("unknown")["items"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"groups": {}},
        {"groups": [{}] * 21},
        {"ingredient_ids": "wrong"},
        {"ingredient_ids": ["../escape"]},
        {"home": "/other"},
        {"provider": "remote"},
        {"revision": 1},
    ],
)
def test_invalid_board_fields_leave_no_records(tmp_path, change):
    _, source, _ = image_source(tmp_path)
    store = BoardStore(tmp_path)
    with pytest.raises(CatalogError):
        store.create(payload(source, **change))
    assert store.list()["total"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"artifact_id": "../escape"},
        {"artifact_version": True},
        {"artifact_version": 0},
        {"artifact_version": 99},
        {"caption": "x" * 2001},
        {"colors": ["red"]},
        {"colors": ["#abc"]},
        {"colors": ["#112233"] * 13},
        {"provenance": {"title": "Invented"}},
        {"credential": "secret"},
    ],
)
def test_invalid_cards_fail_atomically(tmp_path, change):
    _, source, _ = image_source(tmp_path)
    store = BoardStore(tmp_path)
    request = payload(source)
    request["groups"][0]["cards"][0].update(change)
    with pytest.raises(CatalogError):
        store.create(request)
    assert store.list()["items"] == []


def test_duplicate_ids_and_stale_mutations(tmp_path):
    _, source, _ = image_source(tmp_path)
    store = BoardStore(tmp_path)
    request = payload(source)
    request["groups"][0]["cards"].append(
        copy.deepcopy(request["groups"][0]["cards"][0])
    )
    with pytest.raises(CatalogError, match="unique"):
        store.create(request)
    first = store.create(payload(source))
    store.update(first["id"], {"revision": 1, "title": "Latest"})
    with pytest.raises(CatalogError) as error:
        store.update(first["id"], {"revision": 1, "title": "Stale"})
    assert error.value.status == 409
    with pytest.raises(CatalogError) as error:
        store.restore(first["id"], {"revision": 1, "target_revision": 1})
    assert error.value.status == 409
    with pytest.raises(CatalogError) as error:
        store.export(first["id"], 99)
    assert error.value.status == 404
    assert store.export(first["id"])["title"] == "Latest"
    assert len(store.revisions(first["id"])) == 2


def test_actual_http_and_canonical_image_bytes(tmp_path):
    provider, source, png = image_source(tmp_path)
    registry.register_provider(provider)

    async def journey():
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        app.router.add_get("/api/artifacts/{slug}/raw", api_artifact_raw)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(BASE, json=payload(source))
            assert response.status == 201
            first = await response.json()
            path = f"{BASE}/{first['id']}"
            response = await client.get(path)
            record = await response.json()
            preview = await client.get(record["source_status"][0]["preview_url"])
            assert preview.status == 200
            assert preview.headers["Content-Type"] == "image/png"
            assert await preview.read() == png
            response = await client.patch(
                path, json={"revision": 1, "title": "HTTP update"}
            )
            assert response.status == 200
            response = await client.get(path + "/export?revision=1")
            assert response.status == 200
            assert "attachment" in response.headers["Content-Disposition"]
            assert await response.json() == first
            response = await client.post(
                path + "/restore", json={"revision": 2, "target_revision": 1}
            )
            assert (await response.json())["revision"] == 3
            response = await client.get(path + "/revisions")
            assert len((await response.json())["items"]) == 3
            response = await client.get(BASE + "/sources")
            assert (await response.json())["items"][0]["id"] == source.slug
            response = await client.get(BASE + "?q=Atmosphere&limit=1")
            assert (await response.json())["total"] == 1
            response = await client.get(BASE + "?home=bad")
            assert response.status == 400
            response = await client.get(path + "/export?revision=bad")
            assert response.status == 400
            response = await client.post(
                BASE, json={"title": "Hidden", "home": "/other"}
            )
            assert response.status == 400

    try:
        asyncio.run(journey())
    finally:
        registry.unregister_provider("native")
