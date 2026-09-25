import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.graph import UniverseGraph
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider
from gideon.workspace.capabilities.creative.universes import UniverseStore


def universes(home):
    store = UniverseStore(home)
    target = store.create(
        {
            "request_id": "target",
            "title": "Target",
            "canon": [
                {"id": "law", "title": "Law", "body": "No sunrise."},
                {"id": "target-only", "title": "Target only"},
            ],
            "visual_identity": {"colors": ["#112233"], "style_notes": "Copper"},
        }
    )
    source = store.create(
        {
            "request_id": "source",
            "title": "Source",
            "canon": [
                {"id": "law", "title": "Law", "body": "Sunrise allowed."},
                {"id": "source-only", "title": "Source only"},
            ],
            "visual_identity": {"colors": ["#aabbcc"], "style_notes": "Glass"},
        }
    )
    return store, UniverseGraph(store), target, source


def payload(target, source, **changes):
    return {
        "request_id": str(uuid4()),
        "source_id": source["id"],
        "source_revision": source["revision"],
        "target_revision": target["revision"],
        "canon_choices": {"law": "source"},
        "identity_choice": "target",
        **changes,
    }


def test_graph_projects_real_relations_members_external_and_missing(tmp_path):
    catalog = IngredientStore(tmp_path)
    other = catalog.create(
        {"request_id": "other", "title": "Outside city", "type": "place"}
    )
    member = catalog.create(
        {
            "request_id": "member",
            "title": "Character",
            "type": "character",
            "relations": [{"kind": "related", "target_id": other["id"]}],
        }
    )
    store = UniverseStore(tmp_path)
    target = store.create(
        {"request_id": "target", "title": "Universe", "ingredient_ids": [member["id"]]}
    )
    graph = UniverseGraph(store)
    projection = graph.graph(target["id"])
    assert projection["revision"] == 1
    assert projection["merges"] == []
    assert projection["nodes"] == [
        {
            "id": member["id"],
            "title": "Character",
            "type": "character",
            "missing": False,
            "external": False,
        },
        {
            "id": other["id"],
            "title": "Outside city",
            "type": "place",
            "missing": False,
            "external": True,
        },
    ]
    assert projection["edges"] == [
        {"source": member["id"], "target": other["id"], "kind": "related"}
    ]
    store.update(
        target["id"], {"revision": 1, "ingredient_ids": [member["id"], other["id"]]}
    )
    assert all(not node["external"] for node in graph.graph(target["id"])["nodes"])
    with catalog.connection() as db:
        db.execute("DELETE FROM ingredients WHERE id=?", (other["id"],))
    missing = graph.graph(target["id"])
    assert missing["nodes"][1]["missing"]
    assert missing["nodes"][1]["title"] == other["id"]
    assert missing["edges"] == projection["edges"]


def test_merge_preview_resolves_conflicts_and_preserves_source_and_history(tmp_path):
    store, graph, target, source = universes(tmp_path)
    preview = graph.merge_preview(target["id"], {"source_id": source["id"]})
    assert preview["target"] == target
    assert preview["source"] == source
    assert preview["canon_conflicts"] == [
        {"id": "law", "target": target["canon"][0], "source": source["canon"][0]}
    ]
    assert preview["identity_conflict"]
    assert preview["added_canon_ids"] == ["source-only"]
    assert store.export(target["id"]) == target
    merged = graph.merge(target["id"], payload(target, source))
    result = merged["universe"]
    assert result["revision"] == 2
    assert result["title"] == target["title"]
    assert result["canon"][0] == source["canon"][0]
    assert [entry["id"] for entry in result["canon"]] == [
        "law",
        "target-only",
        "source-only",
    ]
    assert result["visual_identity"] == target["visual_identity"]
    assert store.export(source["id"]) == source
    assert store.export(target["id"], 1) == target
    assert store.revisions(target["id"]) == [result, target]
    events = graph.graph(target["id"])["merges"]
    assert events == [merged["merge"]]
    assert events[0]["source_revision"] == 1
    assert events[0]["result_revision"] == 2
    assert graph.graph(source["id"])["merges"] == []
    reopened = UniverseGraph(UniverseStore(tmp_path))
    assert reopened.graph(target["id"])["merges"] == events
    restored = store.restore(target["id"], {"revision": 2, "target_revision": 1})
    assert restored["canon"] == target["canon"]
    assert reopened.graph(target["id"])["merges"] == events


def test_merge_opposite_choices_union_links_and_no_conflict_defaults(tmp_path):
    store, graph, target, source = universes(tmp_path)
    catalog = IngredientStore(tmp_path)
    one = catalog.create({"request_id": "one", "type": "place", "title": "One"})
    two = catalog.create({"request_id": "two", "type": "object", "title": "Two"})
    board = store.boards.create({"request_id": "board", "title": "Board"})
    target = store.update(target["id"], {"revision": 1, "ingredient_ids": [one["id"]]})
    source = store.update(
        source["id"],
        {
            "revision": 1,
            "ingredient_ids": [one["id"], two["id"]],
            "board_refs": [{"id": board["id"], "revision": 1}],
        },
    )
    result = graph.merge(
        target["id"],
        payload(
            target, source, canon_choices={"law": "target"}, identity_choice="source"
        ),
    )["universe"]
    assert result["canon"][0] == target["canon"][0]
    assert result["visual_identity"] == source["visual_identity"]
    assert result["ingredient_ids"] == [one["id"], two["id"]]
    assert result["board_refs"] == source["board_refs"]
    empty = store.create(
        {
            "request_id": "empty",
            "title": "Empty",
            "visual_identity": result["visual_identity"],
        }
    )
    no_conflict = payload(result, empty, canon_choices={})
    del no_conflict["identity_choice"]
    again = graph.merge(target["id"], no_conflict)["universe"]
    assert again["canon"] == result["canon"]
    assert again["ingredient_ids"] == result["ingredient_ids"]
    assert again["revision"] == result["revision"] + 1


@pytest.mark.parametrize(
    "changes",
    [
        {"canon_choices": {}},
        {"canon_choices": {"law": "unknown"}},
        {"canon_choices": {"law": "source", "extra": "target"}},
        {"identity_choice": ""},
        {"identity_choice": "unknown"},
        {"source_revision": 2},
        {"target_revision": 2},
        {"home": "/tmp/escape"},
        {"provider": "override"},
        {"session_id": "other"},
        {"source_id": "../escape"},
    ],
)
def test_invalid_merge_never_changes_either_universe_or_audit(tmp_path, changes):
    store, graph, target, source = universes(tmp_path)
    with pytest.raises(CatalogError):
        graph.merge(target["id"], payload(target, source, **changes))
    assert store.export(target["id"]) == target
    assert store.export(source["id"]) == source
    assert store.revisions(target["id"]) == [target]
    assert graph.graph(target["id"])["merges"] == []


def test_source_target_changes_after_preview_and_missing_scopes(tmp_path):
    store, graph, target, source = universes(tmp_path)
    graph.merge_preview(target["id"], {"source_id": source["id"]})
    newer = store.update(source["id"], {"revision": 1, "title": "New source"})
    with pytest.raises(CatalogError) as stale:
        graph.merge(target["id"], payload(target, source))
    assert stale.value.status == 409
    target = store.update(target["id"], {"revision": 1, "title": "New target"})
    with pytest.raises(CatalogError):
        graph.merge(target["id"], payload({**target, "revision": 1}, newer))
    for source_id in [target["id"], "missing"]:
        with pytest.raises(CatalogError):
            graph.merge_preview(target["id"], {"source_id": source_id})
    foreign = UniverseGraph(UniverseStore(tmp_path / "other"))
    with pytest.raises(CatalogError):
        foreign.graph(target["id"])
    assert graph.graph(target["id"])["merges"] == []


def test_idempotent_replay_and_concurrent_requests_are_transactional(tmp_path):
    store, graph, target, source = universes(tmp_path)
    request = payload(target, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: graph.merge(target["id"], request), range(2)))
    assert results[0] == results[1]
    assert len(store.revisions(target["id"])) == 2
    assert len(graph.graph(target["id"])["merges"]) == 1
    with pytest.raises(CatalogError) as conflict:
        graph.merge(target["id"], {**request, "identity_choice": "source"})
    assert conflict.value.status == 409
    assert store.export(source["id"]) == source
    assert graph.merge(target["id"], request) == results[0]


def test_merge_collection_limit_rolls_back_event_and_snapshot(tmp_path):
    store, graph, target, source = universes(tmp_path)
    target = store.update(
        target["id"],
        {
            "revision": 1,
            "canon": [{"id": f"t-{i}", "title": f"T{i}"} for i in range(100)],
        },
    )
    request = payload(target, source, canon_choices={})
    with pytest.raises(CatalogError):
        graph.merge(target["id"], request)
    assert store.export(target["id"]) == target
    assert graph.graph(target["id"])["merges"] == []
    assert len(store.revisions(target["id"])) == 2


def test_real_http_and_native_merge_surface(tmp_path):
    async def run():
        store, graph, target, source = universes(tmp_path)
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        base = f"/api/capabilities/creative/universes/{target['id']}"
        async with TestClient(TestServer(app)) as client:
            projection = await client.get(base + "/graph")
            assert projection.status == 200
            assert (await projection.json())["nodes"] == []
            preview = await client.post(
                base + "/merge-preview", json={"source_id": source["id"]}
            )
            assert preview.status == 200
            assert (await preview.json())["canon_conflicts"][0]["id"] == "law"
            result = await client.post(base + "/merge", json=payload(target, source))
            assert result.status == 200
            assert (await result.json())["universe"]["revision"] == 2
            invalid = await client.get(base + "/graph?home=other")
            assert invalid.status == 400
            missing = await client.post(
                base + "/merge-preview", json={"source_id": "missing"}
            )
            assert missing.status == 404
        provider = CreativeToolProvider(tmp_path)
        native_graph = await provider.invoke(
            "creative_universe_graph", {"id": target["id"]}
        )
        assert native_graph.success
        assert len(json.loads(native_graph.output)["merges"]) == 1
        native_preview = await provider.invoke(
            "creative_universe_merge_preview",
            {"id": target["id"], "payload": {"source_id": source["id"]}},
        )
        assert native_preview.success
        viewed = json.loads(native_preview.output)
        request = payload(viewed["target"], source, canon_choices={})
        native_merge = await provider.invoke(
            "creative_universe_merge", {"id": target["id"], "payload": request}
        )
        assert native_merge.success, native_merge.error
        assert json.loads(native_merge.output)["universe"]["revision"] == 3
        assert len(graph.graph(target["id"])["merges"]) == 2

    asyncio.run(run())
