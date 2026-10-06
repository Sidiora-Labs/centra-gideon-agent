from __future__ import annotations

import asyncio

import pytest

from gideon.automation.workflows import journal, service, store
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.models import (
    InstanceState,
    NodeInstance,
    RunStatus,
    WorkflowRun,
)
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_status", ["attempted", "committed"])
async def test_paused_recovery_applies_skip_without_replaying_an_uncertain_effect(
    tmp_path, monkeypatch, effect_status
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    marker = tmp_path / "must-not-replay"
    started_at = "2026-01-01T00:00:00+00:00"
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="recovery-loop",
            status=RunStatus.PAUSED,
            started_at=started_at,
            policy_overrides={"attended": False},
            extra={"loop_kind": "general"},
        )
    )
    spec = {
        "name": "recovery-loop",
        "root": {
            "kind": "action",
            "id": "write",
            "config": {"provider": "bash", "with": {"command": f"touch '{marker}'"}},
        },
    }
    store.write_spec(run.id, spec)
    store.write_state(run.id, {"root": NodeInstance(path="root")})
    store.request_pause(run.id)
    journal.Journal(run.id).effect(
        "root",
        idempotency_key="uncertain-write",
        effect_status=effect_status,
        epoch=0,
        node_id="write",
    )
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    supervisor = WorkflowWatchdog(state, EngineServices(cwd=str(tmp_path)))
    try:
        refused = await service.resume_loop_run(run.id, supervisor=supervisor)
        assert refused["code"] == "WF_LOOP_RESUME_RECONCILE_REQUIRED"
        await supervisor._adopt(store.get(run.id))
        controller = supervisor.controller(run.id)
        assert controller is not None
        await asyncio.wait_for(controller._terminal.wait(), timeout=5)
        assert store.get(run.id).status == RunStatus.PAUSED

        result = service.skip_nodes(run.id, ["write"], supervisor=supervisor)
        assert result["ok"] and result["queued"]
        await asyncio.wait_for(controller._terminal.wait(), timeout=5)
        assert store.get(run.id).status == RunStatus.PAUSED
        assert store.pause_requested(run.id)
        assert store.read_state(run.id)["root"].state == InstanceState.SKIPPED
        assert not marker.exists()
        assert service._loop_resume_effects_pending(run.id) == []

        resumed = await service.resume_loop_run(run.id, supervisor=supervisor)
        assert resumed["ok"] and resumed["resumed"]
        await asyncio.wait_for(controller._terminal.wait(), timeout=5)
        assert store.get(run.id).status == RunStatus.COMPLETE
        assert store.get(run.id).started_at == started_at
        assert not marker.exists()
        assert not any(
            event["kind"] == journal.STEP_STARTED for event in journal.ledger(run.id)
        )
    finally:
        await supervisor.stop()
