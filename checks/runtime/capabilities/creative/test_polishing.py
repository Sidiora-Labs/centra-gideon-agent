import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.polishing import PolishingStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider
from gideon.workspace.capabilities.creative.works import WorkStore


def fixture(home, content="The train arived late.\nAn unchanged second line.\n"):
    works = WorkStore(home)
    work = works.create({"request_id": "work", "title": "Polishing work"})
    work = works.draft(
        work["id"], {"request_id": "draft", "revision": 1, "text": content}
    )["work"]
    return works, PolishingStore(works), work


def request(**changes):
    return {
        "request_id": str(uuid4()),
        "revision": 2,
        "start": 0,
        "end": 22,
        "mode": "authored",
        "replacement": "The train arrived late.",
        "instruction": "Correct spelling.",
        **changes,
    }


def propose(store, id, payload):
    return asyncio.run(store.propose(id, payload))


def test_bounded_authored_candidate_diff_review_and_explicit_promotion(tmp_path):
    works, polish, work = fixture(tmp_path)
    base = works.get(work["id"])
    proposal = propose(polish, work["id"], request())
    assert proposal["mode"] == "authored"
    assert proposal["base_revision"] == 2
    assert proposal["base_draft_id"] == work["active_draft_id"]
    assert works.export(work["id"]) == work
    assert len(works.drafts(work["id"])["items"]) == 1
    candidate = works.artifacts.get(proposal["artifact_id"], version=1)
    assert candidate.readonly
    assert candidate.content == "The train arrived late.\nAn unchanged second line.\n"
    detail = polish.get(work["id"], proposal["id"])
    assert detail["original"] == "The train arived late."
    assert detail["replacement"] == "The train arrived late."
    assert "-The train arived late." in detail["diff"]
    assert "+The train arrived late." in detail["diff"]
    assert detail["promotion"] is None
    assert not detail["missing"]
    assert not detail["diff_truncated"]
    promoted = polish.promote(work["id"], proposal["id"], {"revision": 2})
    assert promoted["work"]["revision"] == 3
    assert promoted["draft"]["artifact_id"] == proposal["artifact_id"]
    assert works.get(work["id"])["text"] == candidate.content
    assert len(works.artifacts.list()) == 2
    assert len(works.drafts(work["id"])["items"]) == 2
    assert works.export(work["id"], 2) == work
    assert works.read_draft(work["id"], base["active_draft_id"])["text"] == base["text"]
    assert polish.get(work["id"], proposal["id"])["promotion"] == promoted
    assert polish.promote(work["id"], proposal["id"], {"revision": 2}) == promoted
    assert len(works.drafts(work["id"])["items"]) == 2
    restored = works.restore(work["id"], {"revision": 3, "target_revision": 2})
    assert restored["active_draft_id"] == base["active_draft_id"]
    assert works.get(work["id"])["text"] == base["text"]
    reopened = PolishingStore(WorkStore(tmp_path))
    assert reopened.list(work["id"])["items"] == [proposal]
    assert reopened.get(work["id"], proposal["id"])["promotion"] == promoted


def test_proposal_replay_and_promotion_conflicts(tmp_path):
    works, polish, work = fixture(tmp_path)
    payload = request()
    first = propose(polish, work["id"], payload)
    assert propose(polish, work["id"], payload) == first
    with pytest.raises(CatalogError) as conflict:
        propose(polish, work["id"], {**payload, "replacement": "Different"})
    assert conflict.value.status == 409
    newer = works.update(work["id"], {"revision": 2, "title": "Metadata changed"})
    with pytest.raises(CatalogError) as stale:
        polish.promote(work["id"], first["id"], {"revision": 3})
    assert stale.value.status == 409
    assert works.export(work["id"]) == newer
    assert len(works.drafts(work["id"])["items"]) == 1
    assert polish.get(work["id"], first["id"])["promotion"] is None
    assert len(works.artifacts.list()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"start": -1},
        {"start": True},
        {"end": 0},
        {"end": 999},
        {"revision": 1},
        {"mode": "fake-model"},
        {"replacement": "x" * 8001},
        {"instruction": "x" * 2001},
        {"replacement": "The train arived late."},
        {"home": "other"},
        {"provider": "override"},
        {"model": "override"},
        {"session_id": "private"},
        {"credential": "override"},
        {"mode": "model", "replacement": "Injected model output"},
    ],
)
def test_invalid_proposals_do_not_write_candidates(tmp_path, changes):
    works, polish, work = fixture(tmp_path)
    with pytest.raises(CatalogError):
        propose(polish, work["id"], request(**changes))
    assert polish.list(work["id"])["items"] == []
    assert works.export(work["id"]) == work
    assert len(works.artifacts.list()) == 1


def test_span_cap_and_cross_home_reference_isolation(tmp_path):
    works, polish, work = fixture(tmp_path / "one", "a" * 5000)
    with pytest.raises(CatalogError):
        propose(polish, work["id"], request(start=0, end=4001))
    foreign_works, foreign, foreign_work = fixture(tmp_path / "two")
    candidate = propose(polish, work["id"], request(end=4000))
    with pytest.raises(CatalogError):
        foreign.get(foreign_work["id"], candidate["id"])
    with pytest.raises(CatalogError):
        foreign.promote(work["id"], candidate["id"], {"revision": 2})
    assert foreign.list(foreign_work["id"])["items"] == []
    assert len(foreign_works.artifacts.list()) == 1


def test_missing_candidate_rejects_promotion(tmp_path):
    works, polish, work = fixture(tmp_path)
    proposal = propose(polish, work["id"], request())
    path = tmp_path / "artifacts" / proposal["artifact_id"] / "versions" / "v1.html"
    path.unlink()
    assert polish.get(work["id"], proposal["id"])["missing"]
    with pytest.raises(CatalogError) as error:
        polish.promote(work["id"], proposal["id"], {"revision": 2})
    assert error.value.status == 404
    assert works.export(work["id"]) == work
    assert len(works.drafts(work["id"])["items"]) == 1


def test_no_active_draft_and_invalid_promotion_payload_fail_closed(tmp_path):
    works = WorkStore(tmp_path)
    work = works.create({"request_id": "empty", "title": "No manuscript"})
    polish = PolishingStore(works)
    with pytest.raises(CatalogError):
        propose(polish, work["id"], request(revision=1))
    assert works.artifacts.list() == []
    work = works.draft(
        work["id"],
        {"request_id": "text", "revision": 1, "text": "The train arived late."},
    )["work"]
    proposal = propose(polish, work["id"], request())
    for invalid in ({}, {"revision": True}, {"revision": 2, "provider": "override"}):
        with pytest.raises(CatalogError):
            polish.promote(work["id"], proposal["id"], invalid)
    assert works.export(work["id"]) == work


def test_actual_http_and_native_candidate_workflow(tmp_path):
    async def run():
        works, polish, work = fixture(tmp_path)
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        base = f"/api/capabilities/creative/works/{work['id']}/polishing"
        async with TestClient(TestServer(app)) as client:
            response = await client.post(base, json=request())
            assert response.status == 200
            proposal = await response.json()
            listed = await client.get(base)
            assert (await listed.json())["items"] == [proposal]
            detail = await client.get(base + "/" + proposal["id"])
            assert (await detail.json())["replacement"] == "The train arrived late."
            promoted = await client.post(
                base + "/" + proposal["id"] + "/promote", json={"revision": 2}
            )
            assert promoted.status == 200
            assert (await promoted.json())["work"]["revision"] == 3
            invalid = await client.get(base + "?home=other")
            assert invalid.status == 400
        provider = CreativeToolProvider(tmp_path)

        async def call(action, args):
            result = await provider.invoke("creative_work_polish_" + action, args)
            assert result.success, result.error
            return json.loads(result.output)

        proposal = await call(
            "propose",
            {
                "id": work["id"],
                "payload": request(
                    revision=3, end=23, replacement="The train arrived early."
                ),
            },
        )
        assert (await call("get", {"id": work["id"], "proposal_id": proposal["id"]}))[
            "promotion"
        ] is None
        assert len((await call("list", {"id": work["id"]}))["items"]) == 2
        result = await call(
            "promote",
            {
                "id": work["id"],
                "proposal_id": proposal["id"],
                "payload": {"revision": 3},
            },
        )
        assert result["work"]["revision"] == 4

    asyncio.run(run())


def test_real_configured_model_attempt_records_inference_outcome(tmp_path):
    works, polish, work = fixture(tmp_path)
    payload = request(mode="model")
    del payload["replacement"]
    report = {
        "task": "creative.07",
        "actual_provider_attempt": True,
        "inference_qualified": False,
    }
    try:
        candidate = propose(polish, work["id"], payload)
    except CatalogError as error:
        assert error.status == 503
        assert works.export(work["id"]) == work
        assert polish.list(work["id"])["items"] == []
        report["error"] = str(error)
    else:
        detail = polish.get(work["id"], candidate["id"])
        assert detail["replacement"] != detail["original"]
        assert 0 < len(detail["replacement"]) <= 8000
        assert candidate["mode"] == "model"
        report.update(
            inference_qualified=True,
            candidate_id=candidate["id"],
            summary=candidate["summary"],
        )
    path = Path("/tmp/gideon-creative-07-inference-oss.json")
    path.write_text(json.dumps(report, indent=2) + "\n")
    print("ACTUAL_INFERENCE_EVIDENCE=" + str(path))
