from __future__ import annotations

import asyncio
import json

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.approval_state import chat_approval_id
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.mark.asyncio
async def test_stopping_real_session_manager_withdraws_waiting_chat_approval(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    sessions = ConversationDirectory(AppConfig())
    state = ConsoleState(sessions=sessions, start_time=0.0)
    chat = state.get_or_create_session("chat-pause")
    future = asyncio.get_running_loop().create_future()
    chat._approval_futures["request-1"] = future
    chat.messages.append(
        {
            "role": "permission",
            "cls": json.dumps(
                {"request_id": "request-1", "asked_by": "agent:chat-pause"}
            ),
        }
    )
    state.broadcast_ws(
        "approval", {"id": "request-1", "session": chat.key, "tool": "read_file"}
    )
    approval_id = chat_approval_id(chat.key, "request-1")

    outcome = await sessions.stop_turn("dashboard:chat-pause")

    assert outcome == "idle"
    assert future.result() == "cancelled"
    assert approval_id not in state._pending_approvals
    assert state.ended_as(approval_id) == "cancelled"


@pytest.mark.asyncio
async def test_real_workflow_pause_withdraws_its_pending_approval(
    tmp_path, monkeypatch
):
    from gideon.automation.workflows import store as workflow_store
    from gideon.automation.workflows.controller import EngineServices, RunController
    from gideon.automation.workflows.models import RunStatus, WorkflowRun

    home = tmp_path / "workflow-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0.0)
    run = WorkflowRun(id="run-paused", workflow_name="approval-test")
    workflow_store.create(run)
    approval_id = "approval:run-paused:node-a"
    state._approval_futures[approval_id] = asyncio.get_running_loop().create_future()
    state._hold_approval(
        {
            "id": approval_id,
            "revision": "pause-revision",
            "source": "workflow:run-paused",
            "session": "workflow:run-paused:node-a",
            "asked_by": "run:run-paused",
        }
    )
    controller = RunController(
        run,
        {"name": "approval-test", "root": {"kind": "sequence", "id": "root"}},
        services=EngineServices(attention_state=state),
    )

    await controller._finish(RunStatus.PAUSED)

    assert run.status == RunStatus.PAUSED
    assert approval_id not in state._pending_approvals
    assert state.ended_as(approval_id) == "cancelled"
