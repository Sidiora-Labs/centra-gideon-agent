from __future__ import annotations

import asyncio
import json

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.approval_state import chat_approval_id
from gideon.interfaces.dashboard.state import ConsoleState


def _state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return ConsoleState(ConversationDirectory(AppConfig()), start_time=0.0)


async def test_finished_chat_owner_withdraws_pending_approval(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    session = state.get_or_create_session("chat-ended")
    future = asyncio.get_running_loop().create_future()
    session._approval_futures["tool-request"] = future
    session.messages.append(
        {
            "role": "permission",
            "cls": json.dumps(
                {"request_id": "tool-request", "asked_by": "agent:chat-ended"}
            ),
        }
    )
    state.broadcast_ws(
        "approval",
        {"id": "tool-request", "session": session.key, "tool": "write_file"},
    )
    approval_id = chat_approval_id(session.key, "tool-request")
    entry = state._pending_approvals[approval_id]
    future.set_result("cancelled")

    assert state.refuse_ended_owner(approval_id)
    assert approval_id not in state._pending_approvals
    assert state.ended_as(approval_id) == "cancelled"
    assert (
        state.resolve_approval_revision(approval_id, True, entry["revision"], by=None)
        == "owner_ended"
    )


async def test_recognized_subagent_without_supervisor_fails_closed(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, monkeypatch)
    state._pending_approvals["spawn:child-1"] = {
        "id": "spawn:child-1",
        "revision": "captured",
        "source": "subagent",
        "asked_by": "agent:child-1",
    }
    state._approval_futures["spawn:child-1"] = (
        asyncio.get_running_loop().create_future()
    )

    assert state.refuse_ended_owner("spawn:child-1")
    assert state.ended_as("spawn:child-1") == "cancelled"
