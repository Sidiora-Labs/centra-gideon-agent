from __future__ import annotations

import asyncio
import json

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.inbox import InboxStore, ItemStatus
from gideon.integrations.inbox_service import InboxService
from gideon.interfaces.dashboard.auto_denials import call_fingerprint
from gideon.interfaces.dashboard.state import ConsoleState


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
async def test_expired_workflow_approval_becomes_a_redacted_exact_origin_row(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))

    from gideon.automation.workflows import store as workflow_store
    from gideon.automation.workflows.models import RunOrigin, WorkflowRun

    run_id = "run-denial-origin"
    node_id = "extract"
    trigger_id = "store:source-watch"
    workflow_store.create(
        WorkflowRun(
            id=run_id,
            workflow_name="denial-origin-test",
            origin=RunOrigin(trigger_id=trigger_id),
        )
    )
    state = _state(home)
    approval_id = f"workflow:{run_id}:{node_id}:call-1"
    raw_input = json.dumps({"query": "private-value-should-not-be-stored"})
    request = asyncio.create_task(
        state.request_approval(
            approval_id,
            f"workflow:{run_id}:{node_id}",
            "search",
            tool_input=raw_input,
            session=f"workflow:{run_id}:{node_id}",
            asked_by=f"run:{run_id}",
        )
    )
    await _pending(state, approval_id)
    state.end_approval(approval_id, outcome="expired")
    assert await request is False

    store = state._inbox_svc.inbox
    rows = [item for item in store.items.values() if item.refs.get("auto_denied") is True]
    assert len(rows) == 1
    row = rows[0]
    assert row.refs["reason"] == "expired"
    assert row.refs["run"] == run_id
    assert row.refs["node"] == node_id
    assert row.refs["trigger"] == trigger_id
    assert row.refs["call_fingerprint"] == call_fingerprint("search", raw_input)
    assert row.status not in {ItemStatus.HANDLED.value, ItemStatus.DISMISSED.value}
    serialized = json.dumps(row.to_dict(), sort_keys=True)
    assert "private-value-should-not-be-stored" not in serialized
    reloaded = InboxStore(path=home / "inbox.json")
    reloaded.load()
    assert row.id in reloaded.items
