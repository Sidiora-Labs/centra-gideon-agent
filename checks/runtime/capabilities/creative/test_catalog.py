import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import (
    PREFIX,
    STORE,
    register,
)
from gideon.workspace.capabilities.creative.store import (
    TYPES,
    CatalogError,
    IngredientStore,
)


def payload(**changes):
    return {"type": "character", "title": "Mara", "request_id": str(uuid4()), **changes}


def test_revisions_restore_and_reopen(tmp_path):
    store = IngredientStore(tmp_path)
    first = store.create(payload(body="First memory", tags=["Night", "night"]))
    assert first["revision"] == 1
    assert first["tags"] == ["night"]
    second = store.update(
        first["id"], {"revision": 1, "body": "New memory", "title": "Mara Vale"}
    )
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]
    restored = store.restore(first["id"], {"revision": 2, "target_revision": 1})
    assert restored["body"] == "First memory"
    assert restored["title"] == "Mara"
    assert restored["revision"] == 3
    reopened = IngredientStore(tmp_path)
    assert reopened.get(first["id"]) == restored
    assert reopened.revisions(first["id"]) == [restored, second, first]
    assert reopened.path == tmp_path / "capabilities" / "creative" / "catalog.sqlite3"


def test_create_replay_and_conflicting_request(tmp_path):
    store = IngredientStore(tmp_path)
    request = payload()
    first = store.create(request)
    assert store.create(request) == first
    store.update(first["id"], {"revision": 1, "title": "Changed"})
    assert store.create(request) == first
    assert store.list()["total"] == 1
    with pytest.raises(CatalogError) as error:
        store.create({**request, "title": "Another"})
    assert error.value.status == 409
    assert store.get(first["id"])["revision"] == 2


def test_stale_updates_and_restores_preserve_history(tmp_path):
    store = IngredientStore(tmp_path)
    record = store.create(payload())
    changed = store.update(record["id"], {"revision": 1, "body": "Changed"})
    for action in (
        lambda: store.update(record["id"], {"revision": 1, "title": "Lost"}),
        lambda: store.restore(record["id"], {"revision": 1, "target_revision": 1}),
    ):
        with pytest.raises(CatalogError) as error:
            action()
        assert error.value.status == 409
    assert store.get(record["id"]) == changed
    assert len(store.revisions(record["id"])) == 2
    with pytest.raises(CatalogError) as error:
        store.restore(record["id"], {"revision": 2, "target_revision": 99})
    assert error.value.status == 404
    assert len(store.revisions(record["id"])) == 2


def test_relationship_graph_transactionality(tmp_path):
    store = IngredientStore(tmp_path)
    parent = store.create(payload(title="World", type="place"))
    child = store.create(payload(title="City", type="place"))
    linked = store.update(
        parent["id"],
        {"revision": 1, "relations": [{"kind": "contains", "target_id": child["id"]}]},
    )
    assert linked["relations"][0]["target_id"] == child["id"]
    with pytest.raises(CatalogError, match="cycle"):
        store.update(
            child["id"],
            {
                "revision": 1,
                "relations": [{"kind": "contains", "target_id": parent["id"]}],
            },
        )
    assert store.get(child["id"])["relations"] == []
    assert len(store.revisions(child["id"])) == 1
    related = store.update(
        child["id"],
        {"revision": 1, "relations": [{"kind": "related", "target_id": parent["id"]}]},
    )
    assert related["revision"] == 2
    with pytest.raises(CatalogError, match="missing"):
        store.update(
            child["id"],
            {
                "revision": 2,
                "relations": [{"kind": "related", "target_id": str(uuid4())}],
            },
        )
    with pytest.raises(CatalogError, match="itself"):
        store.update(
            child["id"],
            {
                "revision": 2,
                "relations": [{"kind": "related", "target_id": child["id"]}],
            },
        )
    assert store.get(child["id"]) == related


def test_restore_refuses_new_cycle(tmp_path):
    store = IngredientStore(tmp_path)
    a = store.create(payload())
    b = store.create(payload())
    store.update(
        a["id"],
        {"revision": 1, "relations": [{"kind": "contains", "target_id": b["id"]}]},
    )
    store.update(a["id"], {"revision": 2, "relations": []})
    store.update(
        b["id"],
        {"revision": 1, "relations": [{"kind": "contains", "target_id": a["id"]}]},
    )
    with pytest.raises(CatalogError, match="cycle"):
        store.restore(a["id"], {"revision": 3, "target_revision": 2})
    assert store.get(a["id"])["revision"] == 3
    assert len(store.revisions(a["id"])) == 3


def test_search_filters_pagination_and_all_types(tmp_path):
    store = IngredientStore(tmp_path)
    for index, kind in enumerate(TYPES):
        store.create(
            payload(
                type=kind,
                title=f"Entry {index}",
                body="Moonlight" if index % 2 else "Sunlight",
                tags=["collection", kind],
            )
        )
    assert store.list()["total"] == 6
    assert store.list(q="MOON")["total"] == 3
    assert store.list(tag="COLLECTION")["total"] == 6
    assert store.list(type="object", tag="object")["total"] == 1
    assert store.list(type="object", tag="theme")["total"] == 0
    first = store.list(limit=2)
    second = store.list(offset=2, limit=2)
    third = store.list(offset=4, limit=2)
    ids = [row["id"] for page in (first, second, third) for row in page["items"]]
    assert len(set(ids)) == 6
    assert ids == sorted(ids)
    assert store.list(offset=6)["items"] == []
    assert first["offset"] == 0
    assert second["limit"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"type": "person"},
        {"title": " "},
        {"title": "x" * 201},
        {"body": "x" * 100001},
        {"body": None},
        {"tags": "bad"},
        {"tags": ["x" * 65]},
        {"tags": [str(n) for n in range(33)]},
        {"source_refs": [{"kind": "url", "id": "https://example.com"}]},
        {"source_refs": [{"kind": "artifact", "id": "../secret"}]},
        {"source_refs": [{"kind": "artifact", "id": "valid", "home": "/other"}]},
        {"source_refs": [{"kind": "knowledge", "id": "file:///secret"}]},
        {"relations": [{"kind": "invented", "target_id": "thing"}]},
        {"relations": [{"kind": "related", "target_id": "thing", "model": "x"}]},
        {"home": "/other"},
        {"provider": "other"},
        {"created_at": "yesterday"},
        {"revision": 1},
        {"request_id": "../escape"},
        {"request_id": None},
    ],
)
def test_create_rejects_invalid_values_without_writes(tmp_path, change):
    store = IngredientStore(tmp_path)
    with pytest.raises(CatalogError):
        store.create(payload(**change))
    assert store.list()["total"] == 0


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"limit": True},
        {"type": "bad"},
        {"q": "x" * 201},
    ],
)
def test_query_bounds(tmp_path, query):
    with pytest.raises(CatalogError):
        IngredientStore(tmp_path).list(**query)


def test_two_homes_and_concurrent_revision_writers(tmp_path):
    home = tmp_path / "one"
    first = IngredientStore(home)
    other = IngredientStore(tmp_path / "two")
    record = first.create(payload())
    assert other.list()["total"] == 0
    with pytest.raises(CatalogError) as error:
        other.get(record["id"])
    assert error.value.status == 404

    def update(title):
        try:
            return IngredientStore(home).update(
                record["id"], {"revision": 1, "title": title}
            )
        except CatalogError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["One", "Two"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    assert len(first.revisions(record["id"])) == 2


def test_real_artifact_sources_and_missing_references(tmp_path):
    from gideon.workspace.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(tmp_path / "artifacts")
    artifact = provider.create(name="Source", content="Actual source", kind="markdown")
    store = IngredientStore(tmp_path)
    record = store.create(
        payload(
            source_refs=[
                {"kind": "artifact", "id": artifact.slug},
                {"kind": "knowledge", "id": "missing"},
            ]
        )
    )
    status = store.source_status(record)
    assert status[0] == {"kind": "artifact", "id": artifact.slug, "missing": False}
    assert status[1]["missing"] is True
    assert (
        IngredientStore(tmp_path / "other").source_status(record)[0]["missing"] is True
    )
    assert provider.get(artifact.slug).content == "Actual source"


def test_http_complete_journey(tmp_path):
    async def journey():
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(PREFIX, json=payload())
            assert response.status == 201
            first = await response.json()
            id = first["id"]
            assert first["source_status"] == []
            response = await client.patch(
                f"{PREFIX}/{id}",
                json={
                    "revision": 1,
                    "body": "Changed",
                    "source_refs": [{"kind": "artifact", "id": "absent"}],
                },
            )
            assert response.status == 200
            changed = await response.json()
            assert changed["source_status"][0]["missing"] is True
            response = await client.get(f"{PREFIX}?q=Changed&limit=1")
            assert response.status == 200
            page = await response.json()
            assert page["total"] == 1
            assert page["items"][0]["id"] == id
            response = await client.post(
                f"{PREFIX}/{id}/restore", json={"revision": 2, "target_revision": 1}
            )
            assert response.status == 200
            assert (await response.json())["body"] == ""
            response = await client.get(f"{PREFIX}/{id}/revisions")
            history = (await response.json())["items"]
            assert [row["revision"] for row in history] == [3, 2, 1]
            assert history[1]["body"] == "Changed"
            response = await client.get(f"{PREFIX}/{id}")
            assert (await response.json())["revision"] == 3
        assert IngredientStore(tmp_path).get(id)["revision"] == 3

    asyncio.run(journey())


def test_real_knowledge_reference_resolves_only_its_home(tmp_path):
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    knowledge = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    source = knowledge.create_typed_item(
        item_type="note", title="Field notes", content="The city has seven gates"
    )
    source_id = source["id"] if isinstance(source, dict) else source
    store = IngredientStore(tmp_path)
    record = store.create(payload(source_refs=[{"kind": "knowledge", "id": source_id}]))
    assert store.source_status(record) == [
        {"kind": "knowledge", "id": source_id, "missing": False}
    ]
    assert knowledge.get_item(source_id)["content"] == "The city has seven gates"
    assert (
        IngredientStore(tmp_path / "other").source_status(record)[0]["missing"] is True
    )
    knowledge.db.close()


def test_http_error_contracts(tmp_path):
    async def journey():
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                PREFIX, data="{", headers={"Content-Type": "application/json"}
            )
            assert response.status == 400
            response = await client.post(PREFIX, json=[])
            assert response.status == 400
            response = await client.post(PREFIX, json=payload())
            item = await response.json()
            path = f"{PREFIX}/{item['id']}"
            response = await client.patch(path, json={"revision": 9, "body": "lost"})
            assert response.status == 409
            assert (await response.json())["code"] == "creative_invalid"
            response = await client.get(PREFIX + "/missing")
            assert response.status == 404
            response = await client.get(PREFIX + "?limit=garbage")
            assert response.status == 400
            response = await client.get(PREFIX + "?home=other")
            assert response.status == 400
            response = await client.patch(path, json={"revision": True, "body": "lost"})
            assert response.status == 400
            response = await client.post(
                path + "/restore", json={"revision": 1, "target_revision": 99}
            )
            assert response.status == 404
            response = await client.patch(path, json={"revision": 1})
            assert response.status == 400
            response = await client.get(path + "/revisions")
            assert len((await response.json())["items"]) == 1

    asyncio.run(journey())
