from __future__ import annotations

import asyncio
import json

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.inbox import InboxStore, ItemStatus
from gideon.integrations.inbox_service import InboxService
from gideon.interfaces.dashboard.auto_denials import (
    call_fingerprint,
    owner_reentry_attempt,
    record_auto_denial,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import OWNER, Principal


def _state(home):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0.0)
    inbox = InboxStore(path=home / "inbox.json")
    inbox.load()
    state._inbox_svc = InboxService(store=inbox)
    return state


async def _pending(state, approval_id):
    for _ in range(100):
        entry = state._pending_approvals.get(approval_id)
        if entry is not None:
            return entry
        await asyncio.sleep(0.005)
    raise AssertionError("approval request did not enter the real dashboard registry")


@pytest.mark.asyncio
async def test_only_exact_owner_retried_call_resolves_the_original_note(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = _state(home)
    principal = Principal(OWNER, "owner-session")
    state.get_or_create_session(principal.label)
    run_id = "run-retry"
    node_id = "extract"
    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import RunStatus, WorkflowRun

    store.create(
        WorkflowRun(
            id=run_id, workflow_name="denied-call-retry", status=RunStatus.RUNNING
        )
    )
    session = f"workflow:{run_id}:{node_id}"
    source = session
    tool_input = json.dumps({"query": "find the recorded result"})
    fingerprint = call_fingerprint("search", tool_input)
    note_id = record_auto_denial(
        state,
        session=session,
        call_id="call-expired",
        tool="search",
        fingerprint=fingerprint,
        reason="expired",
        source=source,
    )
    assert note_id

    approval_id = f"{session}:call-retry"

    async def retry(call_input):
        with owner_reentry_attempt(
            state,
            note_id,
            principal,
            origin_kind="workflow",
            origin_id=run_id,
            node_id=node_id,
            attempt_id="rewind:root.extract:1:1",
        ):
            return await state.request_approval(
                approval_id,
                source,
                "search",
                tool_input=call_input,
                session=session,
                asked_by=f"run:{run_id}",
            )

    task = asyncio.create_task(retry(tool_input))
    pending = await _pending(state, approval_id)
    assert pending["_auto_denied_note_id"] == note_id
    assert pending["_call_fingerprint"] == fingerprint
    assert pending["_auto_denied_origin_kind"] == "workflow"
    assert pending["_auto_denied_origin_id"] == run_id
    assert pending["_auto_denied_attempt_id"] == "rewind:root.extract:1:1"
    assert (
        state.resolve_approval_revision(
            approval_id, True, pending["revision"], by=principal
        )
        == "resolved"
    )
    assert await task is True
    row = state._inbox_svc.inbox.items[note_id]
    assert row.status_for("") == ItemStatus.HANDLED.value
    assert row.refs["auto_denied_outcome"] == "approved"

    wrong_input = json.dumps({"query": "different call"})
    second_id = record_auto_denial(
        state,
        session=session,
        call_id="call-second",
        tool="search",
        fingerprint=call_fingerprint("search", wrong_input),
        reason="expired",
        source=source,
    )
    assert second_id and second_id != note_id
    wrong_approval_id = f"{session}:wrong-call"
    wrong_task = asyncio.create_task(
        state.request_approval(
            wrong_approval_id,
            source,
            "search",
            tool_input=wrong_input,
            session=session,
            asked_by=f"run:{run_id}",
        )
    )
    wrong_pending = await _pending(state, wrong_approval_id)
    assert wrong_pending.get("_auto_denied_note_id", "") == ""
    assert (
        state.resolve_approval_revision(
            wrong_approval_id, True, wrong_pending["revision"], by=principal
        )
        == "resolved"
    )
    assert await wrong_task is True
    assert state._inbox_svc.inbox.items[second_id].status_for("") not in {
        ItemStatus.HANDLED.value,
        ItemStatus.DISMISSED.value,
    }


def test_queued_workflow_retry_proof_is_transient_and_epoch_bound(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))

    from gideon.automation.workflows import store
    from gideon.automation.workflows.controller import EngineServices, RunController
    from gideon.automation.workflows.models import (
        InstanceState,
        RunStatus,
        WorkflowRun,
        walk,
    )

    state = _state(home)
    principal = Principal(OWNER, "owner-session")
    node_id = "extract"
    run = store.create(
        WorkflowRun(
            id="run-queued-retry", workflow_name="queued-retry", status=RunStatus.PAUSED
        )
    )
    spec = {
        "name": "queued-retry",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "transform", "id": node_id, "config": {"expr": {"ok": True}}}
            ],
        },
    }
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(attention_state=state)
    )
    target_path = next(
        path for path, node in walk(controller.root) if node.id == node_id
    )
    controller._instance(target_path).state = InstanceState.FAILED
    store.write_state(run.id, controller.instances)
    raw_input = json.dumps({"query": "same raw call"})
    note_id = record_auto_denial(
        state,
        session=f"workflow:{run.id}:{node_id}",
        call_id="call-queued",
        tool="search",
        fingerprint=call_fingerprint("search", raw_input),
        reason="expired",
        source=f"workflow:{run.id}:{node_id}",
    )
    result = controller.submit_mutation(
        [{"op": "rewind", "node_id": node_id}],
        actor="chat",
        confirm=True,
        owner_reentry={
            "note_id": note_id,
            "fingerprint": call_fingerprint("search", raw_input),
            "principal": principal,
            "origin_kind": "workflow",
            "origin_id": run.id,
            "node_id": node_id,
        },
    )
    assert result["queued"] is True
    assert note_id not in json.dumps(run.extra["workflow_queued_mutations"])
    controller._drain_mutations()
    assert len(controller._pending_owner_reentry) == 1
    proof = next(iter(controller._pending_owner_reentry.values()))
    assert proof["node_id"] == node_id
    assert proof["epoch"] == controller.instances[proof["instance_path"]].epoch
    assert proof["attempt"] == controller.instances[proof["instance_path"]].attempt + 1
    assert proof["instance_path"] == target_path

    restored = RunController(
        store.get(run.id),
        store.read_spec(run.id),
        services=EngineServices(attention_state=state),
    )
    assert restored._pending_owner_reentry == {}
    assert all(entry[2] is None for entry in restored._pending_mutations)
