"""Real provider, journal and trigger-store lifecycle checks."""

import asyncio
import logging
import time

import pytest

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.service import retire_after_run, tick, to_iso
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.trigger_dispatch import TriggerAction, TriggerDispatch
from gideon.engine.trigger_outcomes import FireLedger
from gideon.integrations.action_providers.base import ActionContext, ActionResult
from gideon.integrations.action_providers.notify_provider import NotifyActionProvider
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.guardrails.rungs import route_provider_action

LOGGER = logging.getLogger(__name__)


def one_shot(identity, now, *, title="Completed"):
    return Trigger(
        id=identity,
        name=identity,
        kind="clock",
        spec={"kind": "at", "at": to_iso(now - 1), "delete_after_run": True},
        next_fire_at=to_iso(now - 1),
        capabilities={"providers": ["notify"]},
        workflow={"provider": "notify", "config": {"title_template": title}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("title,expected", [("Completed", "success"), ("", "failure")])
async def test_taken_one_shot_survives_until_real_provider_settles(tmp_path, monkeypatch, title, expected):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    now = time.time()
    store = TriggerStore(base_dir=tmp_path)
    trigger = one_shot("clock:completion", now, title=title)
    store.upsert(trigger)
    admitted = await tick(store, now=now)
    assert len(admitted.fires) == 1
    taken = TriggerStore(base_dir=tmp_path).get(trigger.id).trigger
    assert not taken.enabled and not taken.next_fire_at
    assert taken.run_count == 1 and taken.last_fired_at

    runtime = RuntimeCoordinator(AppConfig())
    runtime.dashboard_state = ConsoleState(None, now)
    previous = get_action_services()
    set_action_services(ActionServices(runtime.dashboard_state, asyncio.create_task))
    try:
        action = TriggerAction("notify", trigger.workflow["config"], NotifyActionProvider())
        route = route_provider_action("notify", session_key="trigger:completion")
        before = time.time()
        await TriggerDispatch(runtime, taken, {"scheduled_for": now - 1}, "trigger.fired", LOGGER).execute(
            action, action.config, ActionContext("trigger.fired"), route
        )
        after = time.time()
    finally:
        set_action_services(previous)
    rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
    assert count == 1 and rows[0]["status"] == expected
    assert before <= rows[0]["started_at"] <= rows[0]["finished_at"] <= after
    assert rows[0]["summary"] and rows[0]["run_id"]
    persisted = TriggerStore(base_dir=tmp_path).get(trigger.id)
    if expected == "success":
        assert persisted is None
        detail = await ExecutionJournal(tmp_path).get_run(trigger.id, rows[0]["run_id"])
        assert detail["trace"].startswith("notified:")
    else:
        assert persisted is not None
        assert persisted.trigger.last_run_id == rows[0]["run_id"]
        assert persisted.trigger.run_owner_pid == 0
        assert "title_template" in rows[0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["launched", "queued", "waiting", "interrupted"])
async def test_noncompleted_results_keep_durable_one_shot(tmp_path, monkeypatch, status):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    now = time.time()
    trigger = one_shot("clock:pending", now)
    trigger.enabled = False
    trigger.next_fire_at = ""
    trigger.last_fired_at = to_iso(now)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    runtime = RuntimeCoordinator(AppConfig())
    result = ActionResult(True, outcome=status)
    recorded = await FireLedger(runtime, ExecutionJournal, LOGGER).record(
        trigger, result, None, started=now, finished=now + 2
    )
    assert recorded == status
    assert not retire_after_run(store, trigger, status=recorded)
    assert TriggerStore(base_dir=tmp_path).get(trigger.id) is not None
    rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
    assert count == 1 and rows[0]["duration_ms"] == 2000
    assert not TriggerStore(base_dir=tmp_path).get(trigger.id).trigger.last_success_at


@pytest.mark.asyncio
async def test_missing_provider_is_durably_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    trigger = one_shot("clock:unknown", time.time())
    trigger.workflow = {"provider": "provider-does-not-exist"}
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    await TriggerDispatch(RuntimeCoordinator(AppConfig()), trigger, {}, "trigger.fired", LOGGER).run()
    rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
    assert count == 1 and rows[0]["status"] == "skipped_gate"
    assert "unknown action provider" in rows[0]["error"]
    assert store.get(trigger.id) is not None


def test_retirement_preserves_rearmed_live_row(tmp_path):
    now = time.time()
    stale = one_shot("clock:rearmed", now)
    stale.enabled = False
    stale.next_fire_at = ""
    stale.last_fired_at = to_iso(now)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(stale)
    live = store.get(stale.id).trigger
    live.enabled = True
    live.spec["at"] = to_iso(now + 60)
    live.next_fire_at = to_iso(now + 60)
    store.upsert(live)
    assert not retire_after_run(store, stale, status="success")
    assert store.get(stale.id).trigger.enabled


@pytest.mark.asyncio
async def test_native_workflow_completion_records_terminal_success(tmp_path, monkeypatch):
    from gideon.automation.triggers import grants
    from gideon.automation.workflows import defs
    from gideon.automation.workflows import store as runs
    from gideon.automation.workflows.models import RunStatus
    from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.integrations.action_providers.run_workflow_provider import RunWorkflowActionProvider
    from gideon.security.approval_answer import YOU

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    now = time.time()
    trigger = one_shot("clock:workflow", now)
    trigger.enabled, trigger.next_fire_at, trigger.last_fired_at = False, "", to_iso(now)
    trigger.workflow = {"provider": "run-workflow", "config": {"workflow": "empty"}}
    prior_provider = defs.get_provider("native")
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    await provider.save_def(name="empty", root={"kind": "sequence", "children": []}, provenance="user")
    question = grants.question(trigger)
    assert grants.grant(trigger, confirmed_revision=question.revision, principal=YOU, shown=question.shown)
    TriggerStore(base_dir=tmp_path).upsert(trigger)
    supervisor = WorkflowWatchdog()
    runtime = RuntimeCoordinator(AppConfig())
    state = ConsoleState(None, now)
    previous = get_action_services()
    set_action_services(ActionServices(state, asyncio.create_task, workflows=supervisor))
    try:
        from trigger_origin_fixture import authenticated_origin

        origin = await authenticated_origin()
        action = TriggerAction("run-workflow", trigger.workflow["config"], RunWorkflowActionProvider())
        await TriggerDispatch(runtime, trigger, {}, "trigger.fired", LOGGER).execute(
            action, action.config, ActionContext("trigger.fired", context=trigger.id, trigger_id=trigger.id, accepted_origin=origin),
            route_provider_action("run-workflow", session_key="trigger:workflow"),
        )
        assert len(runtime._handler_tasks) == 1
        assert not TriggerStore(base_dir=tmp_path).get(trigger.id).trigger.last_success_at
        await asyncio.wait_for(asyncio.gather(*runtime._handler_tasks), timeout=5)
        run = runs.active_runs()
        assert not run
        rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert count == 2
        assert {row["status"] for row in rows} == {"launched", "success"}
        assert TriggerStore(base_dir=tmp_path).get(trigger.id) is None
    finally:
        await supervisor.stop()
        set_action_services(previous)
        if prior_provider is None:
            defs.unregister_provider("native")
        else:
            defs.register_provider(prior_provider)


@pytest.mark.asyncio
async def test_native_agent_refusal_and_cancel_are_not_success():
    from gideon.engine.subagent import DelegationSupervisor, SubagentInfo
    from gideon.integrations.action_providers.completion import agent_launch, agent_result

    manager = DelegationSupervisor(None, None)
    refused = manager._refused("task", "", "spawn refused: invalid grant")
    admitted = agent_launch(manager, refused, "launched")
    assert not admitted.success and admitted.completion is None
    cancelled = SubagentInfo("cancelled", "task", done=True, cancelled=True)
    terminal = await agent_result(manager, cancelled)
    assert not terminal.success and terminal.outcome == "interrupted"
