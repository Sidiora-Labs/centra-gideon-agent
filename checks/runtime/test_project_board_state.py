from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.loop import store as loop_store
from gideon.automation.loop.loop import Loop, LoopStatus
from gideon.automation.workflows import store as run_store
from gideon.automation.workflows.models import RunStatus, WorkflowRun
from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes
from gideon.engine.tasks.project_views import BoardProjection


@asynccontextmanager
async def project_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    previous = dict(registry._providers)
    registry._providers.clear()
    try:
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            project = await (
                await client.post("/api/projects", json={"name": "Live board"})
            ).json()
            yield client, project["id"]
    finally:
        registry._providers.clear()
        registry._providers.update(previous)


@pytest.mark.asyncio
async def test_actual_board_distinguishes_provider_fallback_from_source_failure(
    tmp_path, monkeypatch
):
    async with project_server(tmp_path, monkeypatch) as (client, project_id):
        run = run_store.create(
            WorkflowRun(
                id="",
                workflow_name="Persisted run",
                status=RunStatus.RUNNING,
                project_id=project_id,
            )
        )
        loop = loop_store.create(
            Loop(
                id="",
                kind="goal",
                name="Needs operator",
                task="Review the actual project state",
                project_id=project_id,
            )
        )
        loop_store.update_status(loop.id, LoopStatus.BLOCKED)
        tasks_dir = tmp_path / "tasks"
        if tasks_dir.exists():
            tasks_dir.rename(tmp_path / "tasks-saved")
        tasks_dir.write_text("not a directory")
        response = await client.get(f"/api/projects/{project_id}/work")
        body = await response.json()
        assert response.status == 200 and body["completeness"] == "complete"
        assert {row["name"]: row["status"] for row in body["sections"]} == {
            "runs": "ok",
            "loops": "ok",
            "tasks": "ok",
        }
        assert body["board"][0]["state"] == "needs_input"
        assert {row["run_id"] for group in body["board"] for row in group["rows"]} == {
            run.id,
            loop.id,
        }
        assert body["attention"] >= 1
        database = run_store._db_path()
        database.rename(database.with_suffix(".saved"))
        database.mkdir()
        failed = await (await client.get(f"/api/projects/{project_id}/work")).json()
        assert failed["completeness"] == "partial"
        assert {row["name"]: row["status"] for row in failed["sections"]} == {
            "runs": "error",
            "loops": "ok",
            "tasks": "ok",
        }
        assert {
            row["run_id"] for group in failed["board"] for row in group["rows"]
        } == {loop.id}
        linked = await (await client.get(f"/api/projects/{project_id}/linked")).json()
        assert [row["id"] for row in linked["loops"]] == [loop.id]
        assert linked["code"] == linked["chats"] == []


@pytest.mark.asyncio
async def test_claim_receipts_track_real_lease_and_keep_existing_holder(
    tmp_path, monkeypatch
):
    async with project_server(tmp_path, monkeypatch) as (client, project_id):
        prefix = f"/api/projects/{project_id}/work"
        body = {"target_id": "  actual-leaf  ", "holder": " worker-one "}
        first = await (await client.post(prefix + "/claim", json=body)).json()
        assert first["granted"] and first["claim"]["holder"] == "worker-one"
        assert first["claim"]["expires_at"] - first["claim"]["taken_at"] == 300
        second = await (await client.post(prefix + "/claim", json=body)).json()
        assert (
            second["granted"]
            and second["claim"]["renewals"] == first["claim"]["renewals"] + 1
        )
        foreign = await (
            await client.post(
                prefix + "/release",
                json={"target_id": "actual-leaf", "holder": "worker-two"},
            )
        ).json()
        assert not foreign["released"] and foreign["claim"]["holder"] == "worker-one"
        released = await (await client.post(prefix + "/release", json=body)).json()
        assert released == {"released": True, "claim": None, "reason": ""}
        for endpoint in ("claim", "release"):
            assert (await client.post(prefix + "/" + endpoint, json=[])).status == 400
            assert (await client.post(prefix + "/" + endpoint, data="{")).status == 400
            assert (
                await client.post(prefix + "/" + endpoint, json={"holder": "one"})
            ).status == 400


def test_board_decode_uses_public_state_fallback_and_claim_number_coercion():
    row = BoardProjection.decode(
        {
            "run_id": 7,
            "title": None,
            "state": "unrecognized",
            "claim": {"holder": 42, "expires_at": "12.5", "renewals": "3"},
            "attention": "yes",
        }
    )
    assert row.run_id == "7" and row.title == "" and row.state.value == "working"
    assert (
        row.claim.holder == "42"
        and row.claim.expires_at == 12.5
        and row.claim.renewals == 3
    )
    assert row.attention is True
