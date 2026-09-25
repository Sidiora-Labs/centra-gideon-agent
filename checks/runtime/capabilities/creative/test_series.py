import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.sqlite_compat import sqlite3
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider


def payload(**changes):
    return {
        "request_id": str(uuid4()),
        "title": "Night journeys",
        "synopsis": "A traveler searches for home.",
        "volumes": [
            {
                "id": "volume-one",
                "title": "Departure",
                "chapters": [
                    {
                        "id": "chapter-one",
                        "title": "Station",
                        "prompt": "Begin at a station.",
                    },
                    {
                        "id": "chapter-two",
                        "title": "Walk",
                        "prompt": "Continue along the tracks.",
                    },
                ],
            }
        ],
        "arcs": [
            {
                "id": "home",
                "title": "Homecoming",
                "summary": "Learn where home is.",
                "chapter_ids": ["chapter-one", "chapter-two"],
            }
        ],
        **changes,
    }


def write(store, series, chapter="chapter-one", work_revision=1, **changes):
    data = {
        "request_id": str(uuid4()),
        "revision": series["revision"],
        "work_revision": work_revision,
        "mode": "authored",
        "text": "The last train arrived.\n",
        **changes,
    }
    return asyncio.run(store.draft(series["id"], chapter, data))


def test_series_plan_revision_order_arcs_restore_and_reopen(tmp_path):
    store = SeriesStore(tmp_path)
    first = store.create(payload())
    assert first["revision"] == 1
    assert first["arcs"][0]["chapter_ids"] == ["chapter-one", "chapter-two"]
    statuses = store.get(first["id"])["chapter_status"]
    assert [s["stage"] for s in statuses] == ["planned", "planned"]
    assert [s["ready_to_draft"] for s in statuses] == [True, False]
    volumes = copy.deepcopy(first["volumes"])
    volumes[0]["chapters"].reverse()
    volumes.append({"id": "volume-two", "title": "Return", "chapters": []})
    second = store.update(first["id"], {"revision": 1, "volumes": volumes})
    assert second["volumes"][0]["chapters"][0]["id"] == "chapter-two"
    assert len(second["volumes"]) == 2
    assert store.export(first["id"], 1) == first
    restored = store.restore(first["id"], {"revision": 2, "target_revision": 1})
    assert restored["volumes"] == first["volumes"]
    assert restored["arcs"] == first["arcs"]
    assert restored["revision"] == 3
    assert SeriesStore(tmp_path).revisions(first["id"]) == [restored, second, first]


def test_staged_authored_drafts_review_unlock_and_upstream_invalidation(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    first_work = store.prepare(series["id"], "chapter-one", {"revision": 1})
    second_work = store.prepare(series["id"], "chapter-two", {"revision": 1})
    assert first_work["prompt"].endswith("Begin at a station.")
    assert first_work["prompt"].startswith(series["synopsis"])
    assert store.prepare(series["id"], "chapter-one", {"revision": 1}) == first_work
    assert store.works.list()["total"] == 2
    with pytest.raises(CatalogError, match="preceding"):
        write(store, series, chapter="chapter-two")
    assert store.works.artifacts.list() == []
    first = write(store, series)
    assert first["work"]["id"] == first_work["id"]
    assert first["work"]["revision"] == 2
    assert store.get(series["id"])["chapter_status"][0]["stage"] == "drafted"
    assert store.get(series["id"])["chapter_status"][1]["ready_to_draft"] is False
    reviewed = store.review(
        series["id"], "chapter-one", {"revision": 1, "work_revision": 2}
    )
    assert reviewed["reviewed_draft_id"] == first["draft"]["id"]
    assert store.get(series["id"])["chapter_status"][0]["stage"] == "reviewed"
    assert store.get(series["id"])["chapter_status"][1]["ready_to_draft"]
    second = write(
        store, series, chapter="chapter-two", text="She followed the tracks."
    )
    assert second["work"]["id"] == second_work["id"]
    store.review(series["id"], "chapter-two", {"revision": 1, "work_revision": 2})
    assert [s["stage"] for s in store.get(series["id"])["chapter_status"]] == [
        "reviewed",
        "reviewed",
    ]
    revised = write(store, series, work_revision=2, text="An earlier train arrived.")
    assert revised["work"]["revision"] == 3
    status = store.get(series["id"])["chapter_status"]
    assert [s["stage"] for s in status] == ["drafted", "drafted"]
    assert not status[1]["ready_to_draft"]
    store.review(series["id"], "chapter-one", {"revision": 1, "work_revision": 3})
    assert store.get(series["id"])["chapter_status"][1]["stage"] == "drafted"
    store.review(series["id"], "chapter-two", {"revision": 1, "work_revision": 2})
    assert store.get(series["id"])["chapter_status"][1]["stage"] == "reviewed"
    assert (
        store.works.read_draft(first_work["id"], first["draft"]["id"])["text"]
        == "The last train arrived.\n"
    )
    assert len(store.works.drafts(first_work["id"])["items"]) == 2


def test_actual_canonical_previous_draft_and_arc_context(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    for cid in ("chapter-one", "chapter-two"):
        store.prepare(series["id"], cid, {"revision": 1})
    with pytest.raises(CatalogError):
        store.drafting_context(series["id"], "chapter-two")
    result = write(store, series, text="A" * 3000)
    store.review(series["id"], "chapter-one", {"revision": 1, "work_revision": 2})
    context = store.drafting_context(series["id"], "chapter-two")
    assert context["series_title"] == series["title"]
    assert context["chapter"]["id"] == "chapter-two"
    assert context["arcs"] == series["arcs"]
    assert context["prior_reviewed"][0]["draft_id"] == result["draft"]["id"]
    assert context["prior_reviewed"][0]["text"] == "A" * 2000
    assert context["prior_reviewed"][0]["truncated"]
    assert context["prior_reviewed"][0]["original_characters"] == 3000
    assert not context["prior_reviewed"][0]["missing"]
    assert context["omitted_prior_chapters"] == 0
    assert context["work_context"]["prompt"].endswith("Continue along the tracks.")


def test_missing_draft_invalidates_review_and_prevents_next_stage(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    store.prepare(series["id"], "chapter-one", {"revision": 1})
    result = write(store, series)
    store.review(series["id"], "chapter-one", {"revision": 1, "work_revision": 2})
    version = (
        tmp_path / "artifacts" / result["draft"]["artifact_id"] / "versions" / "v1.html"
    )
    version.unlink()
    status = store.get(series["id"])["chapter_status"]
    assert status[0]["draft_missing"]
    assert status[0]["stage"] == "drafted"
    assert not status[1]["ready_to_draft"]
    with pytest.raises(CatalogError) as missing:
        store.review(series["id"], "chapter-one", {"revision": 1, "work_revision": 2})
    assert missing.value.status == 404


def test_draft_request_replay_before_current_revision_and_concurrent_calls(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    store.prepare(series["id"], "chapter-one", {"revision": 1})
    request = {
        "request_id": "retry",
        "revision": 1,
        "work_revision": 1,
        "mode": "authored",
        "text": "First prose",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: asyncio.run(
                    store.draft(series["id"], "chapter-one", request)
                ),
                range(2),
            )
        )
    assert results[0] == results[1]
    assert asyncio.run(store.draft(series["id"], "chapter-one", request)) == results[0]
    assert len(store.works.artifacts.list()) == 1
    with pytest.raises(CatalogError) as conflict:
        asyncio.run(
            store.draft(series["id"], "chapter-one", {**request, "text": "Different"})
        )
    assert conflict.value.status == 409
    with pytest.raises(CatalogError):
        write(store, series, work_revision=1)
    assert len(store.works.drafts(results[0]["work"]["id"])["items"]) == 1


def test_real_prepare_and_draft_sqlite_failure_rollback(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER stop_prepare BEFORE INSERT ON series_chapters BEGIN SELECT RAISE(ABORT, 'prepare fail'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.prepare(series["id"], "chapter-one", {"revision": 1})
    assert store.works.list()["total"] == 0
    with store.connection() as db:
        db.execute("DROP TRIGGER stop_prepare")
    work = store.prepare(series["id"], "chapter-one", {"revision": 1})
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER stop_receipt BEFORE INSERT ON series_draft_requests BEGIN SELECT RAISE(ABORT, 'receipt fail'); END"
        )
    request = {
        "request_id": "recover",
        "revision": 1,
        "work_revision": 1,
        "mode": "authored",
        "text": "Recovered text",
    }
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(store.draft(series["id"], "chapter-one", request))
    assert store.works.get(work["id"])["revision"] == 1
    assert store.works.drafts(work["id"])["items"] == []
    with store.connection() as db:
        db.execute("DROP TRIGGER stop_receipt")
    result = asyncio.run(store.draft(series["id"], "chapter-one", request))
    assert result["work"]["revision"] == 2
    assert len(store.works.artifacts.list()) == 1
    assert len(store.works.drafts(work["id"])["items"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"volumes": [{}] * 21},
        {"arcs": [{}] * 101},
        {"title": ""},
        {"synopsis": "x" * 8001},
        {"home": "other"},
        {"provider": "override"},
        {"arcs": [{"id": "arc", "title": "Bad", "chapter_ids": ["missing"]}]},
    ],
)
def test_invalid_series_plan_is_atomic(tmp_path, changes):
    store = SeriesStore(tmp_path)
    with pytest.raises(CatalogError):
        store.create(payload(**changes))
    assert store.list()["total"] == 0
    original = store.create(payload())
    with pytest.raises(CatalogError):
        store.update(original["id"], {"revision": 1, **changes})
    assert store.export(original["id"]) == original


def test_prepared_plan_immutable_dangling_arc_and_foreign_sources(tmp_path):
    store = SeriesStore(tmp_path)
    author = store.works.authors.create({"request_id": "author", "title": "Writer"})
    series = store.create(payload(author_ref={"id": author["id"], "revision": 1}))
    store.prepare(series["id"], "chapter-one", {"revision": 1})
    volumes = copy.deepcopy(series["volumes"])
    volumes[0]["chapters"][0]["prompt"] = "Rebind prepared plan"
    with pytest.raises(CatalogError, match="pinned"):
        store.update(series["id"], {"revision": 1, "volumes": volumes})
    volumes = copy.deepcopy(series["volumes"])
    volumes[0]["chapters"].pop()
    with pytest.raises(CatalogError, match="missing chapter"):
        store.update(series["id"], {"revision": 1, "volumes": volumes})
    other = SeriesStore(tmp_path / "other")
    with pytest.raises(CatalogError):
        other.prepare(series["id"], "chapter-one", {"revision": 1})
    with pytest.raises(CatalogError):
        other.create(payload(author_ref=series["author_ref"]))
    assert other.works.list()["total"] == 0
    with store.connection() as db:
        db.execute("DELETE FROM author_revisions WHERE id=?", (author["id"],))
    assert store.get(series["id"])["source_status"][0]["missing"]


def test_real_http_and_native_series_paths(tmp_path):
    async def run():
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/capabilities/creative/series", json=payload()
            )
            assert response.status == 201
            series = await response.json()
            path = (
                f"/api/capabilities/creative/series/{series['id']}/chapters/chapter-one"
            )
            prepared = await client.post(path + "/prepare", json={"revision": 1})
            assert prepared.status == 200
            drafted = await client.post(
                path + "/draft",
                json={
                    "request_id": "http",
                    "revision": 1,
                    "work_revision": 1,
                    "mode": "authored",
                    "text": "HTTP manuscript",
                },
            )
            assert drafted.status == 200
            reviewed = await client.post(
                path + "/review", json={"revision": 1, "work_revision": 2}
            )
            assert reviewed.status == 200
            assert (await reviewed.json())["reviewed_draft_id"]
        provider = CreativeToolProvider(tmp_path)

        async def call(action, args):
            result = await provider.invoke("creative_series_" + action, args)
            assert result.success, result.error
            return json.loads(result.output)

        series = await call("create", {"payload": payload()})
        assert (await call("get", {"id": series["id"]}))["revision"] == 1
        await call(
            "prepare",
            {
                "id": series["id"],
                "chapter_id": "chapter-one",
                "payload": {"revision": 1},
            },
        )
        result = await call(
            "draft",
            {
                "id": series["id"],
                "chapter_id": "chapter-one",
                "payload": {
                    "request_id": "native",
                    "revision": 1,
                    "work_revision": 1,
                    "mode": "authored",
                    "text": "Native manuscript",
                },
            },
        )
        assert result["work"]["revision"] == 2
        await call(
            "review",
            {
                "id": series["id"],
                "chapter_id": "chapter-one",
                "payload": {"revision": 1, "work_revision": 2},
            },
        )
        changed = await call(
            "update",
            {"id": series["id"], "payload": {"revision": 1, "title": "Updated series"}},
        )
        assert (await call("list", {"q": "Updated"}))["items"] == [changed]
        assert (await call("revisions", {"id": series["id"]}))["total"] == 2
        assert await call("export", {"id": series["id"], "revision": 1}) == series
        assert (
            await call(
                "restore",
                {"id": series["id"], "payload": {"revision": 2, "target_revision": 1}},
            )
        )["title"] == series["title"]

    asyncio.run(run())


def test_actual_model_drafting_outcome_separate_from_authored_evidence(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    work = store.prepare(series["id"], "chapter-one", {"revision": 1})
    report = {
        "task": "creative.09",
        "actual_provider_attempt": True,
        "inference_qualified": False,
    }
    try:
        result = asyncio.run(
            store.draft(
                series["id"],
                "chapter-one",
                {
                    "request_id": "model",
                    "revision": 1,
                    "work_revision": 1,
                    "mode": "model",
                    "instruction": "Write a short opening paragraph.",
                },
            )
        )
    except CatalogError as error:
        assert error.status == 503
        assert store.works.get(work["id"])["revision"] == 1
        assert store.works.drafts(work["id"])["items"] == []
        report["error"] = str(error)
    else:
        assert result["work"]["revision"] == 2
        assert store.works.get(work["id"])["text"].strip()
        report.update(inference_qualified=True, draft_id=result["draft"]["id"])
    Path("/tmp/gideon-creative-09-inference-oss.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )


def test_prior_context_is_bounded_to_three_actual_reviewed_chapters(tmp_path):
    store = SeriesStore(tmp_path)
    chapters = [
        {"id": f"c-{index}", "title": f"Chapter {index}", "prompt": f"Prompt {index}"}
        for index in range(5)
    ]
    series = store.create(
        payload(
            volumes=[{"id": "volume", "title": "Book", "chapters": chapters}], arcs=[]
        )
    )
    for index in range(5):
        store.prepare(series["id"], f"c-{index}", {"revision": 1})
    for index in range(4):
        result = write(
            store, series, chapter=f"c-{index}", text=f"Canonical chapter {index}"
        )
        assert result["work"]["revision"] == 2
        store.review(series["id"], f"c-{index}", {"revision": 1, "work_revision": 2})
    context = store.drafting_context(series["id"], "c-4")
    assert context["omitted_prior_chapters"] == 1
    assert [item["chapter_id"] for item in context["prior_reviewed"]] == [
        "c-1",
        "c-2",
        "c-3",
    ]
    assert [item["text"] for item in context["prior_reviewed"]] == [
        "Canonical chapter 1",
        "Canonical chapter 2",
        "Canonical chapter 3",
    ]
    assert all(not item["truncated"] for item in context["prior_reviewed"])
    assert context["arcs"] == []
    assert context["chapter"]["id"] == "c-4"
    assert context["work_context"]["prompt"].endswith("Prompt 4")
    assert store.works.list()["total"] == 5


def test_series_drafting_validation_rejects_before_any_manuscript_mutation(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    work = store.prepare(series["id"], "chapter-one", {"revision": 1})
    base = {
        "request_id": "request",
        "revision": 1,
        "work_revision": 1,
        "mode": "authored",
        "text": "Valid text",
    }
    for changes in (
        {"home": "other"},
        {"provider": "override"},
        {"model": "override"},
        {"mode": "invalid"},
        {"mode": "model", "text": "Pretend model text"},
        {"text": ""},
        {"work_revision": True},
        {"revision": 2},
    ):
        with pytest.raises(CatalogError):
            asyncio.run(store.draft(series["id"], "chapter-one", {**base, **changes}))
        assert store.works.get(work["id"])["revision"] == 1
        assert store.works.drafts(work["id"])["items"] == []
    assert store.works.artifacts.list() == []
    for action in (
        lambda: store.prepare(series["id"], "missing", {"revision": 1}),
        lambda: store.review(
            series["id"], "chapter-one", {"revision": 1, "work_revision": 1}
        ),
        lambda: store.prepare(
            series["id"], "chapter-one", {"revision": 1, "session_id": "private"}
        ),
    ):
        with pytest.raises(CatalogError):
            action()
    assert store.works.list()["total"] == 1


def test_missing_canonical_work_marks_chapter_and_prevents_drafting(tmp_path):
    store = SeriesStore(tmp_path)
    series = store.create(payload())
    work = store.prepare(series["id"], "chapter-one", {"revision": 1})
    with store.connection() as db:
        db.execute("DELETE FROM works WHERE id=?", (work["id"],))
    state = store.get(series["id"])["chapter_status"][0]
    assert state["missing"]
    assert state["work_id"] == work["id"]
    assert state["work_revision"] is None
    assert state["stage"] == "planned"
    with pytest.raises(CatalogError):
        write(store, series)
    with pytest.raises(CatalogError):
        store.prepare(series["id"], "chapter-one", {"revision": 1})
    assert store.works.artifacts.list() == []
    assert store.export(series["id"]) == series


def test_series_search_pagination_request_replay_and_stale_metadata(tmp_path):
    store = SeriesStore(tmp_path)
    original_payload = payload(title="Unique series")
    first = store.create(original_payload)
    assert store.create(original_payload) == first
    with pytest.raises(CatalogError) as conflict:
        store.create({**original_payload, "title": "Different"})
    assert conflict.value.status == 409
    second = store.update(first["id"], {"revision": 1, "title": "Updated unique"})
    with pytest.raises(CatalogError):
        store.update(first["id"], {"revision": 1, "title": "Stale"})
    for index in range(26):
        store.create(payload(title=f"Paged {index}"))
    assert store.list()["total"] == 27
    assert len(store.list()["items"]) == 25
    assert len(store.list(offset=25)["items"]) == 2
    assert store.list(q="Updated unique")["items"] == [second]
    assert store.list(q="Nothing")["total"] == 0
