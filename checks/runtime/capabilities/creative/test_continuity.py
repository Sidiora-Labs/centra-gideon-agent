import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.continuity import ContinuityStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider
from gideon.workspace.capabilities.creative.works import WorkStore

SOURCE = "😀 نور carried a red key.\nLater نور carried a blue key."


def fixture(home, source=SOURCE):
    works = WorkStore(home)
    work = works.create({"request_id": "work", "title": "Evidence story"})
    work = works.draft(
        work["id"], {"request_id": "draft", "revision": 1, "text": source}
    )["work"]
    return works, ContinuityStore(works), work


def payload(source=SOURCE, **changes):
    end = source.index("\n") if "\n" in source else len(source)
    anchor = {"start": 0, "end": end, "quote": source[:end]}
    return {
        "request_id": str(uuid4()),
        "work_revision": 2,
        "start": 0,
        "end": len(source),
        "mode": "authored",
        "outline": [{**anchor, "summary": "Noor carries a key"}],
        "facts": [
            {**anchor, "subject": "Noor", "predicate": "key color", "value": "red"}
        ],
        **changes,
    }


def propose(store, id, data):
    return asyncio.run(store.propose(id, data))


def test_reviewed_canonical_evidence_reopens_with_immutable_export(tmp_path):
    works, store, work = fixture(tmp_path)
    original = works.export(work["id"])
    proposal = propose(store, work["id"], payload())
    assert proposal["mode"] == "authored"
    assert proposal["coverage"] == {
        "start": 0,
        "end": len(SOURCE),
        "total_characters": len(SOURCE),
    }
    assert proposal["base_draft_id"] == work["active_draft_id"]
    before = store.get(work["id"])
    assert before["revision"] == 0
    assert before["outline"] == before["facts"] == before["conflicts"] == []
    assert before["proposals"][0]["accepted_revision"] is None
    receipt = store.accept(
        work["id"], proposal["id"], {"revision": 0, "work_revision": 2}
    )
    assert receipt == {"proposal_id": proposal["id"], "revision": 1}
    result = store.export(work["id"])
    assert result["schema_version"] == 1
    assert result["outline"][0]["summary"] == "Noor carries a key"
    assert result["facts"][0]["value"] == "red"
    assert result["facts"][0]["quote"] == SOURCE[: SOURCE.index("\n")]
    assert result["facts"][0]["artifact_id"] == proposal["artifact_id"]
    assert result["facts"][0]["artifact_version"] == 1
    assert not result["facts"][0]["missing"]
    assert not result["facts"][0]["stale"]
    assert ContinuityStore(WorkStore(tmp_path)).export(work["id"]) == result
    assert works.export(work["id"]) == original
    assert works.read_draft(work["id"], work["active_draft_id"])["text"] == SOURCE
    assert len(works.artifacts.list()) == 1
    assert len(works.drafts(work["id"])["items"]) == 1


def test_unicode_exact_offsets_and_duplicate_quote_disambiguation(tmp_path):
    source = "😀 key ثم key"
    works, store, work = fixture(tmp_path, source)
    anchor = {"start": 9, "end": 12, "quote": "key", "summary": "Second key"}
    assert source[9:12] == "key"
    item = propose(store, work["id"], payload(source, outline=[anchor], facts=[]))
    assert item["outline"] == [anchor]
    store.accept(work["id"], item["id"], {"revision": 0, "work_revision": 2})
    assert store.get(work["id"])["outline"][0]["start"] == 9
    bad = {**anchor, "start": 10, "end": 13}
    with pytest.raises(CatalogError):
        propose(store, work["id"], payload(source, outline=[bad], facts=[]))
    assert len(store.get(work["id"])["proposals"]) == 1
    assert works.get(work["id"])["text"] == source


def test_reverse_outline_source_order_and_conflicting_values_preserve_both(tmp_path):
    works, store, work = fixture(tmp_path)
    cut = SOURCE.index("\n") + 1
    later = {"start": cut, "end": len(SOURCE), "quote": SOURCE[cut:]}
    second = propose(
        store,
        work["id"],
        payload(
            outline=[{**later, "summary": "Later blue key"}],
            facts=[
                {**later, "subject": "Noor", "predicate": "key color", "value": "blue"}
            ],
        ),
    )
    first = propose(store, work["id"], payload())
    store.accept(work["id"], second["id"], {"revision": 0, "work_revision": 2})
    store.accept(work["id"], first["id"], {"revision": 1, "work_revision": 2})
    result = store.get(work["id"])
    assert [i["summary"] for i in result["outline"]] == [
        "Noor carries a key",
        "Later blue key",
    ]
    assert [i["accepted_revision"] for i in result["outline"]] == [2, 1]
    assert result["conflicts"] == [
        {
            "subject": "Noor",
            "predicate": "key color",
            "values": ["blue", "red"],
            "proposal_ids": sorted([first["id"], second["id"]]),
        }
    ]
    assert len(result["facts"]) == 2
    assert works.get(work["id"])["revision"] == 2
    assert works.context(work["id"])["universe"] is None


def test_exact_replay_and_changed_request_rejected_after_source_advances(tmp_path):
    works, store, work = fixture(tmp_path)
    data = payload()
    item = propose(store, work["id"], data)
    receipt = store.accept(work["id"], item["id"], {"revision": 0, "work_revision": 2})
    works.draft(
        work["id"], {"request_id": "next", "revision": 2, "text": "Changed text"}
    )
    assert propose(store, work["id"], data) == item
    assert (
        store.accept(work["id"], item["id"], {"revision": 0, "work_revision": 2})
        == receipt
    )
    with pytest.raises(CatalogError) as changed:
        propose(store, work["id"], {**data, "instruction": "Changed"})
    assert changed.value.status == 409
    with pytest.raises(CatalogError) as changed_accept:
        store.accept(work["id"], item["id"], {"revision": 1, "work_revision": 3})
    assert changed_accept.value.status == 409
    result = store.get(work["id"])
    assert result["revision"] == 1
    assert result["facts"][0]["stale"]
    assert result["facts"][0]["quote"].startswith("😀")
    assert not result["facts"][0]["missing"]


def test_unaccepted_stale_source_and_metadata_change_refused(tmp_path):
    works, store, work = fixture(tmp_path)
    item = propose(store, work["id"], payload())
    works.update(work["id"], {"revision": 2, "title": "New title"})
    for expected in (2, 3):
        with pytest.raises(CatalogError) as stale:
            store.accept(
                work["id"], item["id"], {"revision": 0, "work_revision": expected}
            )
        assert stale.value.status == 409
    assert store.get(work["id"])["revision"] == 0
    works.draft(
        work["id"], {"request_id": "next", "revision": 3, "text": "New manuscript"}
    )
    assert store.get(work["id"])["proposals"][0]["stale"]
    assert store.get(work["id"])["facts"] == []


def test_missing_artifact_retains_accepted_exact_quote_and_blocks_new_accept(tmp_path):
    works, store, work = fixture(tmp_path)
    first = propose(store, work["id"], payload())
    other = propose(store, work["id"], payload())
    store.accept(work["id"], first["id"], {"revision": 0, "work_revision": 2})
    works.artifacts.delete(first["artifact_id"])
    result = store.get(work["id"])
    assert all(p["missing"] for p in result["proposals"])
    assert result["facts"][0]["missing"]
    assert result["facts"][0]["quote"] == first["facts"][0]["quote"]
    with pytest.raises(CatalogError) as missing:
        store.accept(work["id"], other["id"], {"revision": 1, "work_revision": 2})
    assert missing.value.status == 404
    with pytest.raises(CatalogError) as missing_proposal:
        propose(store, work["id"], payload())
    assert missing_proposal.value.status == 404
    assert store.get(work["id"])["revision"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"start": -1},
        {"start": True},
        {"end": 0},
        {"end": 999},
        {"work_revision": 1},
        {"mode": "guess"},
        {"home": "foreign"},
        {"provider": "foreign"},
        {"session_id": "other"},
        {"outline": "wrong"},
        {"facts": [None]},
        {"outline": [], "facts": []},
        {"outline": [{"summary": "invented", "start": 0, "end": 2, "quote": "XX"}]},
        {
            "facts": [
                {
                    "subject": "",
                    "predicate": "x",
                    "value": "x",
                    "start": 0,
                    "end": 1,
                    "quote": "😀",
                }
            ]
        },
        {"mode": "model"},
    ],
)
def test_invalid_input_does_not_write_ledger_or_manuscript(tmp_path, change):
    works, store, work = fixture(tmp_path)
    original = works.export(work["id"])
    with pytest.raises(CatalogError):
        propose(store, work["id"], payload(**change))
    assert store.get(work["id"])["proposals"] == []
    assert store.get(work["id"])["revision"] == 0
    assert works.export(work["id"]) == original
    assert len(works.artifacts.list()) == 1


def test_partial_coverage_limits_and_outside_evidence(tmp_path):
    source = "a" * 20001
    works, store, work = fixture(tmp_path, source)
    with pytest.raises(CatalogError, match="20000"):
        propose(store, work["id"], payload(source))
    anchor = {"summary": "Last character", "start": 20000, "end": 20001, "quote": "a"}
    data = payload(source, start=20000, end=20001, outline=[anchor], facts=[])
    item = propose(store, work["id"], data)
    assert item["coverage"] == {"start": 20000, "end": 20001, "total_characters": 20001}
    assert item["outline"][0]["quote"] == "a"
    with pytest.raises(CatalogError):
        propose(
            store, work["id"], {**data, "request_id": "outside", "start": 0, "end": 1}
        )
    assert len(store.get(work["id"])["proposals"]) == 1


def test_concurrent_acceptance_has_one_winner_and_retry_can_follow(tmp_path):
    works, store, work = fixture(tmp_path)
    proposals = [propose(store, work["id"], payload()) for _ in range(2)]

    def accept(item):
        try:
            return ContinuityStore(WorkStore(tmp_path)).accept(
                work["id"], item["id"], {"revision": 0, "work_revision": 2}
            )
        except CatalogError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, proposals))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 409 in results
    loser = proposals[results.index(409)]
    store.accept(work["id"], loser["id"], {"revision": 1, "work_revision": 2})
    assert store.get(work["id"])["revision"] == 2
    assert len(store.get(work["id"])["facts"]) == 2


def test_real_database_failure_rolls_back_acceptance(tmp_path):
    works, store, work = fixture(tmp_path)
    item = propose(store, work["id"], payload())
    with works.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_continuity BEFORE INSERT ON continuity_acceptances BEGIN SELECT RAISE(ABORT,'blocked'); END"
        )
    with pytest.raises(Exception, match="blocked"):
        store.accept(work["id"], item["id"], {"revision": 0, "work_revision": 2})
    assert store.get(work["id"])["revision"] == 0
    assert store.get(work["id"])["facts"] == []
    with works.connection() as db:
        db.execute("DROP TRIGGER reject_continuity")
    assert (
        store.accept(work["id"], item["id"], {"revision": 0, "work_revision": 2})[
            "revision"
        ]
        == 1
    )


async def test_real_http_and_native_current_home_contract(tmp_path):
    works, store, work = fixture(tmp_path)
    app = web.Application()
    app[STORE] = IngredientStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        root = f'/api/capabilities/creative/works/{work["id"]}/continuity'
        response = await client.post(root + "/proposals", json=payload())
        assert response.status == 200
        item = await response.json()
        response = await client.get(root)
        assert (await response.json())["facts"] == []
        response = await client.post(
            root + "/proposals/" + item["id"] + "/accept",
            json={"revision": 0, "work_revision": 2},
        )
        assert response.status == 200
        response = await client.get(root + "/export")
        exported = await response.json()
        assert exported["facts"][0]["value"] == "red"
        assert exported["schema_version"] == 1
        assert (await client.get(root + "?home=other")).status == 400
        assert (
            await client.get("/api/capabilities/creative/works/missing/continuity")
        ).status == 404
        assert (
            await client.post(root + "/proposals", json={"home": "other"})
        ).status == 400
    provider = CreativeToolProvider(tmp_path)
    result = await provider.invoke(
        "creative_work_continuity_export", {"id": work["id"]}
    )
    assert result.success
    assert json.loads(result.output) == exported
    second = await provider.invoke(
        "creative_work_continuity_propose", {"id": work["id"], "payload": payload()}
    )
    assert second.success
    accepted = await provider.invoke(
        "creative_work_continuity_accept",
        {
            "id": work["id"],
            "proposal_id": json.loads(second.output)["id"],
            "payload": {"revision": 1, "work_revision": 2},
        },
    )
    assert accepted.success
    assert json.loads(accepted.output)["revision"] == 2
    foreign = CreativeToolProvider(tmp_path / "foreign")
    denied = await foreign.invoke("creative_work_continuity_get", {"id": work["id"]})
    assert not denied.success and denied.metadata["status"] == 404
    denied = await provider.invoke(
        "creative_work_continuity_get", {"id": work["id"], "home": str(tmp_path)}
    )
    assert not denied.success


async def test_actual_model_attempt_records_unavailable_or_grounded_real_result(
    tmp_path,
):
    works, store, work = fixture(tmp_path)
    data = {
        "request_id": "real-model",
        "work_revision": 2,
        "start": 0,
        "end": len(SOURCE),
        "mode": "model",
        "instruction": "Identify key color with exact evidence.",
    }
    observation = {
        "task": "creative.10",
        "actual_provider_attempt": True,
        "inference_qualified": False,
    }
    try:
        item = await store.propose(work["id"], data)
    except CatalogError as exc:
        observation["error"] = str(exc)
        assert exc.status in (400, 503)
        assert store.get(work["id"])["proposals"] == []
    else:
        assert item["mode"] == "model"
        for entry in item["facts"] + item["outline"]:
            assert entry["quote"] == SOURCE[entry["start"] : entry["end"]]
        assert store.get(work["id"])["facts"] == []
        store.accept(work["id"], item["id"], {"revision": 0, "work_revision": 2})
        observation["inference_qualified"] = True
        observation["proposal_id"] = item["id"]
    Path("/tmp/gideon-creative-10-inference-oss.json").write_text(
        json.dumps(observation, indent=2)
    )
    assert works.get(work["id"])["text"] == SOURCE
