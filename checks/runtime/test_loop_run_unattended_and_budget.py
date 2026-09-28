from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.workflows import service, store, supervisor_policy
from gideon.automation.workflows.context_block import active_workflows_block
from gideon.automation.workflows.loop_view import list_loop_views
from gideon.automation.workflows.models import RunStatus
from gideon.interfaces.dashboard.handlers.loop_routes import (
    api_loop_create,
    api_loop_get,
    api_loop_list,
)
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState

TASK = "Write a three-item checklist for the weekly team update."


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_general_loop_policy_and_run_projection_use_the_real_store():
    result = await service.start_kind_run(
        kind="general",
        inputs={"task": TASK, "exit_condition": "The checklist exists"},
        title="Weekly update checklist",
        policy_overrides={"attended": False, "max_cycles": 1},
        skip_preflight=True,
    )
    assert result["ok"] is False
    assert result["code"] == "WF_NO_SUPERVISOR"

    run = store.get(result["run_id"])
    assert run is not None
    assert run.workflow_name == "general-project"
    assert run.extra["loop_kind"] == "general"
    assert run.extra["loop_name"] == "Weekly update checklist"
    assert run.policy_overrides == {"attended": False, "max_cycles": 1}

    policy = supervisor_policy.policy_for_run(
        "general", overrides=run.policy_overrides
    )
    assert policy.hitl_posture.value == "afk"
    assert supervisor_policy.tick_config(policy).max_cycles == 1

    run.status = RunStatus.RUNNING
    store.save(run)
    listed = store.list_loop_runs(kind="general")
    assert [item.id for item in listed] == [run.id]
    assert [row["run_id"] for row in list_loop_views(kind="general")] == [run.id]
    assert run.id in [item.id for item in store.active_runs()]
    assert f"run_id: {run.id}" in active_workflows_block()

    app = web.Application()
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state.workflows = WorkflowWatchdog(state)
    app["state"] = state
    app.router.add_post("/api/loops", api_loop_create)
    app.router.add_get("/api/loops", api_loop_list)
    app.router.add_get("/api/loops/{id}", api_loop_get)
    try:
        async with TestClient(TestServer(app)) as client:
            refused = await client.post(
                "/api/loops",
                json={
                    "kind": "general",
                    "task": TASK,
                    "auto_teardown_on_complete": True,
                },
            )
            assert refused.status == 400
            assert "Scratch workspace teardown" in (await refused.json())["errors"][0]

            response = await client.get("/api/loops?kind=general")
            assert response.status == 200
            rows = (await response.json())["loops"]
            projected = [row for row in rows if row.get("run_id") == run.id]
            assert len(projected) == 1
            assert projected[0]["name"] == "Weekly update checklist"
            detail = await client.get(f"/api/loops/{run.id}")
            assert detail.status == 200
            body = await detail.json()
            assert body["run_id"] == run.id
            assert body["attended"] is False
            assert body["max_cycles"] == 1
    finally:
        await state.workflows.stop()
