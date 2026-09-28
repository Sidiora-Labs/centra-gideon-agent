from __future__ import annotations

import pytest

from gideon.automation.workflows import journal, store
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.engine import _ask_payload
from gideon.automation.workflows.human_input import Ask, AskKind, list_continuations
from gideon.automation.workflows.models import (
    InstanceState, Node, NodeKind, OriginKind, RunOrigin, RunStatus, WorkflowRun,
)
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.security.approval_answer import OWNER, Principal

RESPONDER = Principal(OWNER, "qualifier").label
PARK = "root.children[0]"
AFTER = "root.children[1]"


def event_spec():
    return {
        "name": "event-gated",
        "root": {
            "kind": "sequence", "id": "s",
            "children": [
                {"kind": "gate", "id": "park", "config": {
                    "kind": "event", "prompt": "Parked until the build finishes.",
                    "risk": "safe", "timeout_secs": 600,
                }},
                {"kind": "transform", "id": "after", "config": {"expr": "the run carried on"}},
            ],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    False,
    "CI should have finished by now: start from the checks tab",
    {"build": 4521},
    {"revise": {"step_ref": "after", "comment": "delete everything instead"}},
])
async def test_event_payload_wakes_real_run_without_answering_or_revising(tmp_path, monkeypatch, payload):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    spec = event_spec()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    watchdog = WorkflowWatchdog(services=EngineServices())
    controller = await watchdog.launch(run, spec)
    try:
        assert await controller.wait_for_terminal(timeout=10) == RunStatus.NEEDS_INPUT
        (pending,) = list_continuations(run.id)
        assert pending.ask["kind"] == AskKind.EVENT.value
        result = controller.resume(pending.token, payload, responder=RESPONDER, always_allow=True)
        assert result["ok"] is True
        assert result["woken"] is True
        assert result["approved"] is False
        assert await controller.wait_for_terminal(timeout=10) == RunStatus.COMPLETE
        assert controller.instances[PARK].state == InstanceState.DONE
        assert store.read_output(run.id, PARK)["answer"] == payload
        assert store.read_output(run.id, AFTER) == "the run carried on"
        assert store.read_spec(run.id)["root"] == spec["root"]
        assert len(controller._allow_memory) == 0
        assert list_continuations(run.id) == []
        resolved = journal.journal_records(run.id, kinds={journal.GATE_RESOLVED})
        assert len(resolved) == 1
        assert resolved[0]["outcome"] == "woken"
        assert resolved[0]["event_woken"] is True
        assert controller.resume(pending.token, payload, responder=RESPONDER)["ok"] is False
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_unattended_event_gate_waits_for_payload(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    spec = event_spec()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], origin=RunOrigin(kind=OriginKind.SCHEDULE)))
    store.write_spec(run.id, spec)
    watchdog = WorkflowWatchdog(services=EngineServices())
    controller = await watchdog.launch(run, spec)
    try:
        assert await controller.wait_for_terminal(timeout=10) == RunStatus.NEEDS_INPUT
        assert controller.instances[PARK].state == InstanceState.WAITING
        assert store.read_output(run.id, AFTER) is None
        (pending,) = list_continuations(run.id)
        assert controller.resume(pending.token, "the build finished", responder=RESPONDER)["woken"] is True
        assert await controller.wait_for_terminal(timeout=10) == RunStatus.COMPLETE
    finally:
        await watchdog.stop()


@pytest.mark.parametrize("payload", ["a message", "", True, False, None, 0, [], {"k": "v"}])
def test_event_ask_accepts_any_payload(payload):
    assert Ask(kind=AskKind.EVENT).validate_answer(payload) == ""


def test_event_gate_kind_overrides_authored_ask_kind():
    event = Node(kind=NodeKind.GATE, id="park", config={"kind": "event", "ask_kind": "text"})
    form = Node(kind=NodeKind.GATE, id="q", config={"kind": "approval", "ask_kind": "form"})
    assert _ask_payload(event, event.config)["kind"] == AskKind.EVENT.value
    assert _ask_payload(form, form.config)["kind"] == AskKind.FORM.value
    assert Ask(kind=AskKind.APPROVAL).validate_answer("ship it") == "approval expects a boolean (or {approved: bool})"


def test_parked_run_card_is_an_event_not_a_decision():
    from gideon.automation.workflows.attention import ask_body
    from gideon.automation.workflows.needs_input import BlockKind, build_item

    item = build_item(run_id="r", node_id="park", ask={"kind": "event"}, now=1_700_000_000.0)
    assert item.block_kind is BlockKind.EVENT
    assert item.to_dict()["block_kind"] == "event"
    assert item.actionable is False
    assert ask_body({"kind": "event"}, None) == "Parked until something wakes it. You can wake it now."


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_kind,changed_target", [("event", False), ("approval", False), ("event", True)])
async def test_real_scheduled_event_wake_obeys_current_action_and_ask_binding(tmp_path, monkeypatch, gate_kind, changed_target):
    import asyncio
    import copy
    import time

    from gideon.automation.triggers import grants, loop, wakeup
    from gideon.automation.triggers.models import Outcome, Trigger
    from gideon.automation.triggers.service import DueFire
    from gideon.automation.triggers.store import TriggerStore
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.action_providers.services import ActionServices, get_action_services, set_action_services
    from gideon.interfaces.dashboard.state import ConsoleState

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    spec = event_spec()
    spec["root"]["children"][0]["config"]["kind"] = gate_kind
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], origin=RunOrigin(kind=OriginKind.MANUAL)))
    store.write_spec(run.id, spec)
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=time.time())
    watchdog = WorkflowWatchdog(state=state, services=EngineServices())
    prior_services = get_action_services()
    set_action_services(ActionServices(state=state, spawn_background=asyncio.create_task, workflows=watchdog))
    controller = await watchdog.launch(run, spec)
    try:
        assert await controller.wait_for_terminal(timeout=10) == RunStatus.NEEDS_INPUT
        (pending,) = list_continuations(run.id)
        trigger = Trigger(
            id="file:scheduled-event-wake", name="Wake the parked run", kind="clock",
            spec={"kind": "cron", "expr": "*/15 * * * *", "timezone": "UTC"},
            workflow={"resume": {"run_id": run.id, "answer": False}},
        )
        trigger_store = TriggerStore()
        trigger_store.save_all([trigger])
        persisted = trigger_store.get(trigger.id)
        assert persisted is not None and persisted.ok
        assert grants.is_granted(persisted.trigger)
        assert grants.action_revision(persisted.trigger)
        fire = DueFire(trigger=persisted.trigger, scheduled_for=time.time() - 1)
        scheduled_wake = wakeup.wakeup_for(fire, seq=1, now=time.time())
        assert scheduled_wake.kind == wakeup.WakeKind.RESUME.value
        assert controller.resume(pending.token, False, responder=f"trigger:{trigger.id}")["ok"] is False
        if changed_target:
            changed = copy.deepcopy(persisted.trigger)
            changed.workflow["resume"]["run_id"] = "another-run"
            trigger_store.save_all([changed])
        result = await loop._apply_resume(scheduled_wake, now=time.time())
        if gate_kind == "event" and not changed_target:
            assert result.outcome == Outcome.RAN.value, (result.reason, result.reported)
            assert await controller.wait_for_terminal(timeout=10) == RunStatus.COMPLETE
            assert store.read_output(run.id, PARK)["answer"] is False
            assert store.read_output(run.id, AFTER) == "the run carried on"
            resolved = journal.journal_records(run.id, kinds={journal.GATE_RESOLVED})
            assert len(resolved) == 1 and resolved[0]["outcome"] == "woken"
            assert len(controller._allow_memory) == 0
        else:
            assert result.outcome == Outcome.REFUSED.value
            assert controller.instances[PARK].state == InstanceState.WAITING
            assert store.read_output(run.id, AFTER) is None
            assert list_continuations(run.id)[0].token == pending.token
            assert journal.journal_records(run.id, kinds={journal.GATE_RESOLVED}) == []
    finally:
        await watchdog.stop()
        set_action_services(prior_services)


@pytest.mark.asyncio
async def test_trigger_answer_response_masks_real_dispatch_failure(tmp_path, monkeypatch):
    import json

    from gideon.automation.triggers.models import Trigger
    from gideon.interfaces.dashboard.handlers.triggers import (
        _dispatch_store_action, _trigger_answer_response,
    )

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    canary = "sk-" + "a" * 48
    trigger = Trigger(
        id="masked-result", kind="manual", name="Missing provider",
        workflow={"provider": canary, "config": {}},
    )
    ran, note = await _dispatch_store_action(trigger, {}, event="manual.answer")
    assert ran is False
    assert canary in note
    response = _trigger_answer_response(ran, note)
    assert response.status == 200
    body = json.loads(response.body)
    assert body["ok"] is False
    assert body["outcome"] == "failed"
    assert "unknown action provider" in body["result"]
    assert canary not in body["result"]
    assert body["result"].count("[REDACTED: credential]") == 1
