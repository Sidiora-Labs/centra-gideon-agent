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
    raise AssertionError(
        f"approval request did not enter the real dashboard registry: {state._pending_approvals}; transcripts: {[s.messages for s in state._sessions.values()]}"
    )


@pytest.mark.asyncio
async def test_only_exact_owner_retried_call_resolves_the_original_note(
    tmp_path, monkeypatch
):
    import uuid

    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import RunStatus, WorkflowRun
    from gideon.interfaces.dashboard.auto_denials import bind_current_reentry_call
    from gideon.security.approval_answer import APP

    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = _state(home)
    principal = Principal(OWNER, "owner-session")
    foreign = Principal(APP, "untrusted-app")
    state.get_or_create_session(principal.label)
    run_id, node_id = "run-retry", "extract"
    store.create(
        WorkflowRun(
            id=run_id, workflow_name="denied-call-retry", status=RunStatus.RUNNING
        )
    )
    session = f"workflow:{run_id}:{node_id}"
    chat_session = state.get_or_create_session(session)
    tool_input = json.dumps({"query": "find the recorded result"})
    fingerprint = call_fingerprint("search", tool_input)
    note_id = record_auto_denial(
        state,
        session=session,
        call_id="call-expired",
        tool="search",
        fingerprint=fingerprint,
        reason="expired",
        source=session,
    )
    assert note_id
    reentry = dict(
        origin_kind="workflow",
        origin_id=run_id,
        node_id=node_id,
        attempt_id="rewind:root.extract:1:1",
        session=session,
    )
    with owner_reentry_attempt(state, note_id, foreign, **reentry) as refused:
        assert refused is None
        assert bind_current_reentry_call(state, fingerprint, session=session) is None
    with owner_reentry_attempt(state, note_id, principal, **reentry) as active:
        assert active is not None
        assert (
            bind_current_reentry_call(
                state,
                call_fingerprint("search", {"query": "mismatch"}),
                session=session,
            )
            is None
        )
        other_state = _state(home)
        assert (
            bind_current_reentry_call(other_state, fingerprint, session=session) is None
        )
        assert (
            bind_current_reentry_call(state, fingerprint, session="different-session")
            is None
        )
        bound = bind_current_reentry_call(state, fingerprint, session=session)
        assert bound is active
        assert bind_current_reentry_call(state, fingerprint, session=session) is None

        request_id = uuid.uuid4().hex
        state.broadcast_ws(
            "approval",
            {
                "session": session,
                "id": request_id,
                "tool": "search",
                "tool_input": tool_input,
                "_call_fingerprint": fingerprint,
                "_auto_denied_note_id": bound.note_id,
                "_auto_denied_origin_kind": bound.origin_kind,
                "_auto_denied_origin_id": bound.origin_id,
                "_auto_denied_attempt_id": bound.attempt_id,
                "_auto_denied_node_id": bound.node_id,
            },
        )
        future = asyncio.get_running_loop().create_future()
        chat_session._approval_futures[request_id] = future
        approval_id = f"{session}:{request_id}"
        pending = await _pending(state, approval_id)
        assert pending["_auto_denied_note_id"] == note_id
        assert pending["_call_fingerprint"] == fingerprint
        assert pending["_auto_denied_origin_kind"] == "workflow"
        assert pending["_auto_denied_origin_id"] == run_id
        assert pending["_auto_denied_attempt_id"] == reentry["attempt_id"]
        assert (
            state.resolve_approval_revision(
                approval_id, True, "stale-revision", by=principal
            )
            == "revision_conflict"
        )
        assert not future.done()
        assert (
            state.resolve_approval_revision(
                approval_id, True, pending["revision"], by=foreign
            )
            != "resolved"
        )
        assert not future.done()
        assert state._inbox_svc.inbox.items[note_id].status_for("") not in {
            ItemStatus.HANDLED.value,
            ItemStatus.DISMISSED.value,
        }
        assert (
            state.resolve_approval_revision(
                approval_id, True, pending["revision"], by=principal
            )
            == "resolved"
        )
        assert await future == "approved"
        chat_session._approval_futures.pop(request_id)
    row = state._inbox_svc.inbox.items[note_id]
    assert row.status_for("") == ItemStatus.HANDLED.value
    assert row.refs["auto_denied_outcome"] == "approved"
    durable = InboxStore(path=home / "inbox.json")
    durable.load()
    assert durable.items[note_id].status_for("") == ItemStatus.HANDLED.value
    with owner_reentry_attempt(state, note_id, principal, **reentry) as closed:
        assert closed is None
        assert bind_current_reentry_call(state, fingerprint, session=session) is None

    wrong_input = json.dumps({"query": "different call"})
    second_id = record_auto_denial(
        state,
        session=session,
        call_id="call-second",
        tool="search",
        fingerprint=call_fingerprint("search", wrong_input),
        reason="expired",
        source=session,
    )
    assert second_id and second_id != note_id
    assert (
        bind_current_reentry_call(
            state, call_fingerprint("search", wrong_input), session=session
        )
        is None
    )
    wrong_request = uuid.uuid4().hex
    state.broadcast_ws(
        "approval",
        {
            "session": session,
            "id": wrong_request,
            "tool": "search",
            "tool_input": wrong_input,
            "_call_fingerprint": call_fingerprint("search", wrong_input),
        },
    )
    wrong_future = asyncio.get_running_loop().create_future()
    chat_session._approval_futures[wrong_request] = wrong_future
    wrong_id = f"{session}:{wrong_request}"
    wrong_pending = await _pending(state, wrong_id)
    assert wrong_pending.get("_auto_denied_note_id", "") == ""
    assert (
        state.resolve_approval_revision(
            wrong_id, True, wrong_pending["revision"], by=principal
        )
        == "resolved"
    )
    assert await wrong_future == "approved"
    chat_session._approval_futures.pop(wrong_request)
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
