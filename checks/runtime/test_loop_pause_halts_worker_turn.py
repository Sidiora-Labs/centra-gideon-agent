from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.workflows import journal as journal_mod, service, store
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.models import RunStatus, WorkflowRun
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.loop_routes import (
    api_loop_action,
    api_loop_get,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.integrations.action_providers.registry import (
    _ensure_default_providers_registered,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


async def wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the real workflow state did not reach the expected point")


@pytest.mark.asyncio
async def test_pause_waits_for_real_action_process_and_resume_survives_restart(
    isolated_home,
):
    _ensure_default_providers_registered()
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state.workflows = WorkflowWatchdog(
        state, EngineServices(cwd=str(isolated_home))
    )
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/loops/{id}", api_loop_get)
    app.router.add_patch("/api/loops/{id}", api_loop_action)

    started = isolated_home / "action-started"
    finished = isolated_home / "action-finished"
    command = (
        f"printf started > '{started}'; sleep 60; "
        f"printf finished > '{finished}'"
    )
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            status=RunStatus.DRAFT,
            policy_overrides={"attended": False, "max_cycles": 1},
            extra={"loop_kind": "general", "loop_name": "Pause proof"},
        )
    )
    spec = {
        "name": "general-project",
        "root": {
            "kind": "action",
            "id": "write",
            "config": {"provider": "bash", "with": {"command": command}},
        },
    }
    store.write_spec(run.id, spec)

    try:
        async with TestClient(TestServer(app)) as client:
            started_response = await client.patch(
                f"/api/loops/{run.id}", json={"action": "start"}
            )
            assert started_response.status == 200
            await wait_until(started.exists)
            controller = state.workflows.controller(run.id)
            assert controller is not None
            assert controller._inflight and not controller._inflight["root"].task.done()

            paused = await asyncio.wait_for(
                client.patch(f"/api/loops/{run.id}", json={"action": "pause"}),
                timeout=8.0,
            )
            assert paused.status == 200
            paused_view = await paused.json()
            assert paused_view["status"] == "paused"
            assert store.get(run.id).status == RunStatus.PAUSED
            assert store.pause_requested(run.id)
            assert not finished.exists()

            await state.workflows.stop()
            state.workflows = WorkflowWatchdog(
                state, EngineServices(cwd=str(isolated_home))
            )
            assert store.pause_requested(run.id)
            resumed = await client.patch(
                f"/api/loops/{run.id}", json={"action": "resume"}
            )
            assert resumed.status == 409
            refusal = await resumed.json()
            assert refusal["error"]["code"] == "loop_resume_reconcile_required"
            assert "write" in refusal["error"]["message"]
            assert "reconcile" in refusal["error"]["message"]
            assert "skip the affected node" in refusal["error"]["message"]
            assert refusal["error"]["detail"]["blocked_effects"][0]["node"] == "write"
            assert store.get(run.id).status == RunStatus.PAUSED
            assert store.pause_requested(run.id)
            assert state.workflows.controller(run.id) is None
            assert not finished.exists()
    finally:
        await state.workflows.stop()


@pytest.mark.parametrize("effect_status", ["attempted", "committed"])
@pytest.mark.asyncio
async def test_paused_loop_journal_effect_without_completed_replay_stays_paused(
    isolated_home, effect_status
):
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            status=RunStatus.PAUSED,
            extra={"loop_kind": "general", "loop_name": "Journal proof"},
        )
    )
    store.request_pause(run.id)
    journal_mod.Journal(run.id).effect(
        "root",
        idempotency_key=f"effect-{effect_status}",
        effect_status=effect_status,
        epoch=0,
        node_id="write",
    )

    result = await service.resume_loop_run(run.id)

    assert result["ok"] is False
    assert result["code"] == "WF_LOOP_RESUME_RECONCILE_REQUIRED"
    assert result["blocked_effects"] == [
        {"node": "write", "epoch": 0, "effect_status": effect_status}
    ]
    assert "reconcile" in result["message"]
    assert "skip the affected node" in result["message"]
    assert store.get(run.id).status == RunStatus.PAUSED
    assert store.pause_requested(run.id)
