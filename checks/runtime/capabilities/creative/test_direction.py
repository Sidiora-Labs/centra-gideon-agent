import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.sqlite_compat import sqlite3
from gideon.interfaces.dashboard.handlers.capabilities_creative_direction import (
    PREFIX,
    register,
)
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.direction_tools import (
    DirectionTools,
    create_provider,
)
from gideon.workspace.capabilities.creative.store import CatalogError
from gideon.workspace.capabilities.creative.works import WorkStore


def prepared(home):
    works = WorkStore(home)
    work = works.create(
        {
            "request_id": "work",
            "title": "Signal",
            "kind": "work",
            "prompt": "",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    result = works.draft(
        work["id"],
        {
            "request_id": "draft",
            "revision": 1,
            "text": "The lighthouse answered.",
            "note": "approved",
        },
    )
    return works, result["work"], result["draft"]


def steps():
    return [
        {
            "id": "verify",
            "title": "Verify source",
            "operation": "source.verify",
            "depends_on": [],
        },
        {
            "id": "publish",
            "title": "Publish treatment",
            "operation": "treatment.snapshot",
            "depends_on": ["verify"],
        },
    ]


def payload(work, **changes):
    return {
        "request_id": str(uuid4()),
        "name": "Night signal",
        "treatment": "Cold blue light crosses a winter harbor.",
        "sources": [{"kind": "work", "id": work["id"], "revision": work["revision"]}],
        "steps": steps(),
        **changes,
    }


def test_pins_exact_canonical_work_and_idempotently_reopens(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    request = payload(work, request_id="direction-one")
    first = store.create(request)
    again = store.create(request)
    assert first == again
    assert first["status"] == "draft" and first["revision"] == 1
    assert first["sources"][0]["id"] == work["id"]
    pin = first["sources"][0]["chapters"][0]
    assert pin == {
        "chapter_id": None,
        "title": "Signal",
        "work_id": work["id"],
        "work_revision": 2,
        "draft_id": draft["id"],
        "artifact_id": draft["artifact_id"],
        "artifact_version": 1,
        "content_hash": hashlib.sha256(b"The lighthouse answered.").hexdigest(),
    }
    assert DirectionStore(tmp_path).get(first["id"]) == first
    assert DirectionStore(tmp_path).list() == [first]
    changed = copy.deepcopy(request)
    changed["name"] = "Conflict"
    with pytest.raises(CatalogError) as conflict:
        store.create(changed)
    assert conflict.value.status == 409
    assert len(store.list()) == 1


def test_series_sources_use_ordered_creative13_tuple_and_reject_unprepared(tmp_path):
    store = DirectionStore(tmp_path)
    series = store.series.create(
        {
            "request_id": "series",
            "title": "Harbor cycle",
            "synopsis": "Signals cross a harbor.",
            "volumes": [
                {
                    "id": "v1",
                    "title": "Winter",
                    "chapters": [
                        {"id": "c1", "title": "Beacon", "prompt": "Light the beacon."},
                        {"id": "c2", "title": "Reply", "prompt": "Answer it."},
                    ],
                }
            ],
            "arcs": [
                {
                    "id": "arc",
                    "title": "Signals",
                    "summary": "A reply arrives.",
                    "chapter_ids": ["c1", "c2"],
                }
            ],
            "author_ref": None,
            "universe_ref": None,
        }
    )
    with pytest.raises(CatalogError, match="not prepared"):
        store.create(
            {
                "request_id": "unprepared",
                "name": "Direction",
                "treatment": "Treat it.",
                "sources": [{"kind": "series", "id": series["id"], "revision": 1}],
                "steps": steps(),
            }
        )
    one = store.series.prepare(series["id"], "c1", {"revision": 1})
    two = store.series.prepare(series["id"], "c2", {"revision": 1})
    asyncio.run(
        store.series.draft(
            series["id"],
            "c1",
            {
                "request_id": "c1-draft",
                "revision": 1,
                "work_revision": 1,
                "mode": "authored",
                "text": "Beacon prose",
                "note": "",
            },
        )
    )
    store.series.review(series["id"], "c1", {"revision": 1, "work_revision": 2})
    asyncio.run(
        store.series.draft(
            series["id"],
            "c2",
            {
                "request_id": "c2-draft",
                "revision": 1,
                "work_revision": 1,
                "mode": "authored",
                "text": "Reply prose",
                "note": "",
            },
        )
    )
    store.series.review(series["id"], "c2", {"revision": 1, "work_revision": 2})
    project = store.create(
        {
            "request_id": "series-direction",
            "name": "Cycle direction",
            "treatment": "Keep the signals stark.",
            "sources": [{"kind": "series", "id": series["id"], "revision": 1}],
            "steps": steps(),
        }
    )
    source = project["sources"][0]
    assert source["title"] == "Harbor cycle"
    assert [row["chapter_id"] for row in source["chapters"]] == ["c1", "c2"]
    assert [row["work_id"] for row in source["chapters"]] == [one["id"], two["id"]]
    assert all(
        set(row)
        == {
            "chapter_id",
            "title",
            "work_id",
            "work_revision",
            "draft_id",
            "artifact_id",
            "artifact_version",
            "content_hash",
        }
        for row in source["chapters"]
    )
    assert (
        source["chapters"][0]["content_hash"]
        == hashlib.sha256(b"Beacon prose").hexdigest()
    )


def test_pause_restart_edit_remaining_resume_and_open_real_output(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(payload(work, request_id="lifecycle"))
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    assert project["status"] == "running"
    project = store.advance(project["id"], {"revision": 2})
    assert project["steps"][0]["status"] == "done"
    verified = project["steps"][0]["result"]
    assert verified["verified_sources"] == [
        {"kind": "work", "id": work["id"], "revision": 2}
    ]
    project = store.control(project["id"], {"revision": 3, "action": "pause"})
    assert project["status"] == "paused"
    reopened = DirectionStore(tmp_path)
    edited = steps() + [
        {
            "id": "release-check",
            "title": "Verify release source",
            "operation": "source.verify",
            "depends_on": ["publish"],
        }
    ]
    project = reopened.replace_plan(project["id"], {"revision": 4, "steps": edited})
    assert project["steps"][0]["result"] == verified
    assert project["steps"][0]["status"] == "done"
    assert project["steps"][2]["status"] == "pending"
    project = reopened.control(project["id"], {"revision": 5, "action": "resume"})
    project = reopened.advance(project["id"], {"revision": 6})
    assert project["steps"][1]["status"] == "done"
    output = project["steps"][1]["result"]
    artifact = reopened.artifacts.get(
        output["artifact_id"], version=output["artifact_version"]
    )
    assert artifact and artifact.readonly
    assert "Cold blue light" in artifact.content
    assert work["id"] in artifact.content
    assert (
        output["content_hash"] == hashlib.sha256(artifact.content.encode()).hexdigest()
    )
    assert output["path"] == f"/api/artifacts/{output['artifact_id']}?version=1"
    project = DirectionStore(tmp_path).advance(project["id"], {"revision": 7})
    assert project["status"] == "completed"
    assert [row["status"] for row in project["steps"]] == ["done", "done", "done"]
    assert project["steps"][0]["result"] == verified


def test_completed_steps_cannot_be_changed_or_removed_and_plan_is_validated(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(payload(work))
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    project = store.advance(project["id"], {"revision": 2})
    project = store.control(project["id"], {"revision": 3, "action": "pause"})
    changed = steps()
    changed[0]["title"] = "Rewrite completed work"
    with pytest.raises(CatalogError, match="cannot be changed"):
        store.replace_plan(project["id"], {"revision": 4, "steps": changed})
    remaining = [
        {
            "id": "publish",
            "title": "Publish treatment",
            "operation": "treatment.snapshot",
            "depends_on": [],
        }
    ]
    with pytest.raises(CatalogError, match="cannot be removed"):
        store.replace_plan(project["id"], {"revision": 4, "steps": remaining})
    invalids = [
        [{"id": "a", "title": "A", "operation": "unknown", "depends_on": []}],
        [
            {
                "id": "a",
                "title": "A",
                "operation": "source.verify",
                "depends_on": ["later"],
            },
            {
                "id": "later",
                "title": "Later",
                "operation": "source.verify",
                "depends_on": [],
            },
        ],
        [
            {
                "id": "same",
                "title": "A",
                "operation": "source.verify",
                "depends_on": [],
            },
            {
                "id": "same",
                "title": "B",
                "operation": "source.verify",
                "depends_on": [],
            },
        ],
    ]
    for invalid in invalids:
        with pytest.raises(CatalogError):
            store.replace_plan(project["id"], {"revision": 4, "steps": invalid})
    assert store.get(project["id"]) == project


def test_controls_revision_and_state_are_optimistic(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(payload(work))
    with pytest.raises(CatalogError) as stale:
        store.control(project["id"], {"revision": 8, "action": "start"})
    assert stale.value.status == 409
    with pytest.raises(CatalogError):
        store.control(project["id"], {"revision": 1, "action": "pause"})
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    with pytest.raises(CatalogError):
        store.replace_plan(project["id"], {"revision": 2, "steps": steps()})
    with pytest.raises(CatalogError):
        store.advance(project["id"], {"revision": 1})
    project = store.advance(project["id"], {"revision": 2})
    with pytest.raises(CatalogError):
        (
            store.advance(project["id"], {"revision": 3})
            if False
            else store.control(project["id"], {"revision": 3, "action": "resume"})
        )
    assert store.get(project["id"]) == project


def test_series_source_change_blocks_verification_without_false_completion(tmp_path):
    store = DirectionStore(tmp_path)
    series = store.series.create(
        {
            "request_id": "s",
            "title": "Changing",
            "synopsis": "",
            "volumes": [
                {
                    "id": "v",
                    "title": "V",
                    "chapters": [{"id": "c", "title": "C", "prompt": "Draft"}],
                }
            ],
            "arcs": [],
            "author_ref": None,
            "universe_ref": None,
        }
    )
    store.series.prepare(series["id"], "c", {"revision": 1})
    asyncio.run(
        store.series.draft(
            series["id"],
            "c",
            {
                "request_id": "d1",
                "revision": 1,
                "work_revision": 1,
                "mode": "authored",
                "text": "First",
                "note": "",
            },
        )
    )
    project = store.create(
        {
            "request_id": "p",
            "name": "Pinned",
            "treatment": "Direction",
            "sources": [{"kind": "series", "id": series["id"], "revision": 1}],
            "steps": steps(),
        }
    )
    asyncio.run(
        store.series.draft(
            series["id"],
            "c",
            {
                "request_id": "d2",
                "revision": 1,
                "work_revision": 2,
                "mode": "authored",
                "text": "Second",
                "note": "",
            },
        )
    )
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    with pytest.raises(CatalogError, match="changed"):
        store.advance(project["id"], {"revision": 2})
    unchanged = store.get(project["id"])
    assert unchanged["steps"][0]["status"] == "pending"
    assert unchanged["status"] == "running"
    assert store.artifacts.list() == [
        item
        for item in store.artifacts.list()
        if not item.slug.startswith("creative-direction-")
    ]


def test_missing_sources_and_artifacts_fail_without_project(tmp_path):
    store = DirectionStore(tmp_path)
    bad = {
        "request_id": "bad",
        "name": "Bad",
        "treatment": "Direction",
        "sources": [{"kind": "work", "id": "missing", "revision": 1}],
        "steps": steps(),
    }
    with pytest.raises(CatalogError) as absent:
        store.create(bad)
    assert absent.value.status == 404 and store.list() == []
    works, work, draft = prepared(tmp_path / "missing-artifact")
    artifact = (
        tmp_path
        / "missing-artifact"
        / "artifacts"
        / draft["artifact_id"]
        / "versions"
        / "v1.html"
    )
    artifact.unlink()
    missing = DirectionStore(tmp_path / "missing-artifact")
    with pytest.raises(CatalogError) as absent_artifact:
        missing.create(payload(work))
    assert absent_artifact.value.status == 404 and missing.list() == []


def test_concurrent_request_is_one_project_and_sqlite_failure_rolls_back(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    request = payload(work, request_id="concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.create(request), range(2)))
    assert results[0] == results[1] and len(store.list()) == 1
    with store.db() as db:
        db.execute(
            "CREATE TRIGGER reject_direction BEFORE INSERT ON direction_projects BEGIN SELECT RAISE(ABORT,'no'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.create(payload(work, request_id="rollback"))
    assert len(store.list()) == 1
    with store.db() as db:
        db.execute("DROP TRIGGER reject_direction")
    assert store.create(payload(work, request_id="rollback"))["id"]
    assert len(store.list()) == 2


def test_real_owner_http_lifecycle_and_native_approval(tmp_path):
    async def run():
        works, work, draft = prepared(tmp_path)
        store = DirectionStore(tmp_path)

        @web.middleware
        async def owner(request, handler):
            request["user"] = "owner"
            return await handler(request)

        app = web.Application(middlewares=[owner])
        app["creative_direction_factory"] = lambda: store
        register(app)
        async with TestClient(TestServer(app)) as client:
            created = await client.post(PREFIX, json=payload(work, request_id="http"))
            assert created.status == 201
            project = await created.json()
            assert (await (await client.get(PREFIX)).json())["items"] == [project]
            assert (
                await (await client.get(PREFIX + "/" + project["id"])).json() == project
            )
            started = await client.post(
                PREFIX + "/" + project["id"] + "/control",
                json={"revision": 1, "action": "start"},
            )
            assert started.status == 200
            project = await started.json()
            advanced = await client.post(
                PREFIX + "/" + project["id"] + "/advance", json={"revision": 2}
            )
            assert advanced.status == 200
            project = await advanced.json()
            assert project["steps"][0]["status"] == "done"
            stale = await client.post(
                PREFIX + "/" + project["id"] + "/control",
                json={"revision": 2, "action": "pause"},
            )
            assert stale.status == 409
            paused = await client.post(
                PREFIX + "/" + project["id"] + "/control",
                json={"revision": 3, "action": "pause"},
            )
            assert paused.status == 200
            edited = steps() + [
                {
                    "id": "release",
                    "title": "Release verification",
                    "operation": "source.verify",
                    "depends_on": ["publish"],
                }
            ]
            saved = await client.post(
                PREFIX + "/" + project["id"] + "/plan",
                json={"revision": 4, "steps": edited},
            )
            assert saved.status == 200
            assert (await saved.json())["steps"][0]["status"] == "done"
        provider = DirectionTools(store)
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert not definitions["creative_direction_projects"].requires_approval
        assert definitions["creative_direction_action"].requires_approval
        assert definitions["creative_direction_action"].risk_level.value == "caution"
        listed = await provider.invoke("creative_direction_projects", {})
        assert listed.success and len(json.loads(listed.output)["items"]) == 1
        native = await provider.invoke(
            "creative_direction_action",
            {"action": "create", "payload": payload(work, request_id="native")},
        )
        assert native.success
        assert (
            json.loads(native.output)["sources"][0]["chapters"][0]["artifact_id"]
            == draft["artifact_id"]
        )
        invalid = await provider.invoke(
            "creative_direction_action", {"action": "wrong", "payload": {}}
        )
        assert not invalid.success
        manifest = json.loads(
            Path(
                "runtime/gideon/extensions/apps/native/gideon-creative-direction/app.json"
            ).read_text()
        )
        assert manifest["name"] == "gideon-creative-direction"
        assert (
            manifest["provider"]["implementation"]
            == "gideon.workspace.capabilities.creative.direction_tools:create_provider"
        )
        registered = create_provider({})
        registered_tools = {tool.name: tool for tool in await registered.list_tools()}
        assert registered_tools["creative_direction_action"].requires_approval

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"treatment": ""},
        {"sources": []},
        {"sources": [{"kind": "artifact", "id": "x", "revision": 1}]},
        {"steps": []},
        {"admin": True},
    ],
)
def test_invalid_create_is_atomic(tmp_path, change):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    request = payload(work)
    request.update(change)
    with pytest.raises(CatalogError):
        store.create(request)
    assert store.list() == []


def test_exact_work_revision_remains_verifiable_after_later_draft(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(payload(work, request_id="stable-revision"))
    newer = works.draft(
        work["id"],
        {
            "request_id": "later-draft",
            "revision": 2,
            "text": "A changed active manuscript.",
            "note": "",
        },
    )["work"]
    assert newer["revision"] == 3
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    project = store.advance(project["id"], {"revision": 2})
    assert project["steps"][0]["status"] == "done"
    assert project["sources"][0]["chapters"][0]["draft_id"] == draft["id"]
    assert (
        project["sources"][0]["chapters"][0]["content_hash"]
        == hashlib.sha256(b"The lighthouse answered.").hexdigest()
    )


def test_conflicting_treatment_artifact_blocks_step_receipt(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(
        payload(
            work,
            request_id="artifact-conflict",
            steps=[
                {
                    "id": "publish",
                    "title": "Publish",
                    "operation": "treatment.snapshot",
                    "depends_on": [],
                }
            ],
        )
    )
    slug = "creative-direction-" + project["id"] + "-publish"
    store.artifacts.create(
        name="conflict",
        slug=slug,
        kind="markdown",
        content="wrong",
        description="wrong",
        readonly=True,
    )
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    with pytest.raises(CatalogError) as conflict:
        store.advance(project["id"], {"revision": 2})
    assert conflict.value.status == 500
    unchanged = store.get(project["id"])
    assert unchanged["steps"][0]["status"] == "pending"
    assert unchanged["steps"][0]["result"] is None


def test_concurrent_advance_completes_each_revision_once(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(payload(work, request_id="advance-once"))
    project = store.control(project["id"], {"revision": 1, "action": "start"})

    def advance():
        try:
            return store.advance(project["id"], {"revision": 2})
        except CatalogError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: advance(), range(2)))
    successes = [row for row in results if isinstance(row, dict)]
    failures = [row for row in results if isinstance(row, CatalogError)]
    assert len(successes) == 1 and len(failures) == 1
    assert failures[0].status == 409
    assert store.get(project["id"])["steps"][0]["attempts"] == 1


def test_single_step_plan_finishes_and_cannot_restart(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    one = [
        {
            "id": "verify",
            "title": "Verify",
            "operation": "source.verify",
            "depends_on": [],
        }
    ]
    project = store.create(payload(work, request_id="single", steps=one))
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    project = store.advance(project["id"], {"revision": 2})
    assert project["status"] == "completed"
    assert project["steps"][0]["attempts"] == 1
    with pytest.raises(CatalogError):
        store.control(project["id"], {"revision": 3, "action": "start"})
    with pytest.raises(CatalogError):
        store.advance(project["id"], {"revision": 3})


def test_http_requires_dashboard_owner_and_reports_missing_project(tmp_path):
    async def run():
        app = web.Application()
        app["creative_direction_factory"] = lambda: DirectionStore(tmp_path)
        register(app)
        async with TestClient(TestServer(app)) as client:
            forbidden = await client.get(PREFIX)
            assert forbidden.status == 403

        @web.middleware
        async def owner(request, handler):
            request["user"] = "owner"
            return await handler(request)

        owned = web.Application(middlewares=[owner])
        owned["creative_direction_factory"] = lambda: DirectionStore(tmp_path)
        register(owned)
        async with TestClient(TestServer(owned)) as client:
            missing = await client.get(PREFIX + "/missing")
            assert missing.status == 404
            assert await missing.json() == {
                "error": "Creative direction project not found"
            }

    asyncio.run(run())


def test_plan_replacement_preserves_completed_attempt_and_timestamp(tmp_path):
    works, work, draft = prepared(tmp_path)
    store = DirectionStore(tmp_path)
    project = store.create(payload(work, request_id="preserve-result"))
    project = store.control(project["id"], {"revision": 1, "action": "start"})
    project = store.advance(project["id"], {"revision": 2})
    completed = copy.deepcopy(project["steps"][0])
    project = store.control(project["id"], {"revision": 3, "action": "pause"})
    replacement = steps() + [
        {
            "id": "final",
            "title": "Final source check",
            "operation": "source.verify",
            "depends_on": ["publish"],
        }
    ]
    project = store.replace_plan(project["id"], {"revision": 4, "steps": replacement})
    assert project["steps"][0] == completed
    assert project["steps"][1]["attempts"] == 0
    assert project["steps"][2]["depends_on"] == ["publish"]
    assert project["status"] == "paused"
