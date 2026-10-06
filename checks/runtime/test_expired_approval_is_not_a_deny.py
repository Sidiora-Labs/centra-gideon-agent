from __future__ import annotations

import asyncio
import json

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.mark.asyncio
async def test_expiry_is_a_distinct_terminal_outcome(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0.0)
    approval_id = "workflow:run-expired:node-a"
    future = asyncio.get_running_loop().create_future()
    state._approval_futures[approval_id] = future
    entry = {
        "id": approval_id,
        "revision": "captured-revision",
        "source": "workflow:run-expired",
        "session": "workflow:run-expired:node-a",
        "asked_by": "run:run-expired",
    }
    state._hold_approval(entry)
    from gideon.security.approval_answer import YOU

    assert (
        state.resolve_approval_revision(approval_id, True, "stale-revision", by=YOU)
        == "revision_conflict"
    )
    assert not future.done()

    state.end_approval(approval_id, outcome="expired")

    assert future.result() is False
    assert state.ended_as(approval_id) == "expired"
    assert (
        state.resolve_approval_revision(
            approval_id, False, "captured-revision", by=None
        )
        == "expired"
    )


@pytest.mark.asyncio
async def test_captured_revision_resolves_pending_owner_decision(tmp_path, monkeypatch):
    from gideon.automation.workflows import store as workflow_store
    from gideon.automation.workflows.models import WorkflowRun
    from gideon.security.sel import sel

    home = tmp_path / "owner-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    workflow_store.create(WorkflowRun(id="run-approved", workflow_name="approval-test"))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0.0)
    approval_id = "workflow:run-approved:node-a"
    future = asyncio.get_running_loop().create_future()
    state._approval_futures[approval_id] = future
    state._hold_approval(
        {
            "id": approval_id,
            "revision": "owner-revision",
            "source": "workflow:run-approved",
            "session": "workflow:run-approved:node-a",
            "asked_by": "run:run-approved",
        }
    )
    from gideon.security.approval_answer import YOU

    assert (
        state.resolve_approval_revision(approval_id, True, "owner-revision", by=YOU)
        == "resolved"
    )
    assert future.result() is True
    assert state.ended_as(approval_id) == "approved"
    owner_row = next(
        json.loads(line)
        for line in sel()._path.read_text(encoding="utf-8").splitlines()
        if line and json.loads(line).get("request_id") == approval_id
    )
    assert owner_row["metadata"]["decided_by"] == "you"

    from gideon.security.approval_answer import on_channel

    workflow_store.create(WorkflowRun(id="run-channel", workflow_name="approval-test"))
    channel_id = "workflow:run-channel:node-a"
    channel_future = asyncio.get_running_loop().create_future()
    state._approval_futures[channel_id] = channel_future
    state._hold_approval(
        {
            "id": channel_id,
            "revision": "channel-revision",
            "source": "workflow:run-channel",
            "session": "workflow:run-channel:node-a",
            "asked_by": "run:run-channel",
        }
    )
    assert (
        state.resolve_approval_revision(
            channel_id, True, "channel-revision", by=on_channel("telegram", "12")
        )
        == "resolved"
    )
    rows = [
        json.loads(line)
        for line in sel()._path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    channel_row = next(row for row in rows if row.get("request_id") == channel_id)
    assert channel_row["metadata"]["decided_by"] == "channel:12"
