from __future__ import annotations

import asyncio
import json

from gideon.automation.loop import manager, store
from gideon.automation.loop.kinds import worker_approval_posture
from gideon.automation.loop.loop import Loop, LoopStatus
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.approval_state import chat_approval_id
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import YOU, agent, asker_of_chat
from gideon.security.guardrails.policy import HEADLESS, INTERACTIVE, is_unattended_session, profile_for_session


def test_loop_worker_rearms_with_current_attended_approval_posture(tmp_path, monkeypatch):
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"
    home = tmp_path / "operator-home"
    for path in (tenant_a, tenant_b, home):
        path.mkdir()
    (tenant_a / "config.json").write_text("{}", encoding="utf-8")
    (tenant_b / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(tenant_a))

    loop = store.create(
        Loop(
            id="a1b2c3d4",
            name="Attended approval posture",
            kind="goal",
            task="write a report after owner approval",
            attended=True,
        )
    )
    main_key = manager.session_key(loop.id)
    task_key = manager.task_session_key(loop.id, "task-1")

    assert not is_unattended_session(main_key)
    assert not is_unattended_session(task_key)
    assert profile_for_session(main_key).name == INTERACTIVE.name
    assert profile_for_session(task_key).name == INTERACTIVE.name
    assert profile_for_session(main_key, unattended=True).name == HEADLESS.name
    assert profile_for_session("loop-deadbeef").name == HEADLESS.name

    unattended = worker_approval_posture(Loop(id="deadbeef", name="U", kind="goal", task="t"))
    attended = worker_approval_posture(loop)
    assert (attended.trust, attended.unattended, attended.acp_mode, attended.approval_policy) == (
        False,
        False,
        "",
        "",
    )
    assert (unattended.trust, unattended.unattended, unattended.acp_mode, unattended.approval_policy) == (
        True,
        True,
        "bypassPermissions",
        "auto",
    )

    sessions = ConversationDirectory(AppConfig.load())
    state = ConsoleState(sessions, start_time=0)
    worker_sessions = [
        state.get_or_create_session(name=key, agent="gideon-loop", app="loop")
        for key in (main_key, task_key)
    ]
    for session in worker_sessions:
        session._trust = True
        session._trust_reads = True
        session._unattended = True
        session.acp_mode = "bypassPermissions"
        manager._arm_worker_approval_posture(state, session, loop)
        assert not session._trust
        assert not session._trust_reads
        assert not session._unattended
        assert session.acp_mode == ""

    async def owner_approval_path():
        session = worker_sessions[1]
        request_id = "attended-write-1"
        future = asyncio.get_running_loop().create_future()
        session._approval_futures[request_id] = future
        created_by_app = str(
            getattr(session, "_app", "")
            or getattr(session, "created_by_app", "")
            or ""
        )
        session.append(
            "permission",
            "write_file",
            json.dumps(
                {
                    "request_id": request_id,
                    "tool_call_id": "",
                    "tool_kind": "",
                    "can_revise": False,
                    "asked_by": asker_of_chat(
                        session.key, created_by_app=created_by_app
                    ).label,
                }
            ),
        )
        state.broadcast_ws(
            "approval",
            {"session": session.key, "id": request_id, "tool": "write_file"},
        )
        approval_id = chat_approval_id(session.key, request_id)
        pending = state._pending_approvals[approval_id]
        assert pending["asked_by"] == "app:loop"
        assert not future.done()
        assert not state.resolve_session_approval(
            session, request_id, "approved", by=agent("intruder")
        )
        assert not future.done()
        assert state.resolve_session_approval(
            session, request_id, "approved", by=YOU
        )
        assert await future == "approved"

    asyncio.run(owner_approval_path())

    monkeypatch.setenv("GIDEON_HOME", str(tenant_b))
    store.create(
        Loop(
            id="a1b2c3d4",
            name="Same id, separate tenant",
            kind="goal",
            task="remain headless",
            attended=False,
        )
    )
    assert profile_for_session(main_key).name == HEADLESS.name
    monkeypatch.setenv("GIDEON_HOME", str(tenant_a))
    assert profile_for_session(main_key).name == INTERACTIVE.name

    async def pending_owner_request():
        session = worker_sessions[1]
        request_id = "attended-write-2"
        future = asyncio.get_running_loop().create_future()
        session._approval_futures[request_id] = future
        state.broadcast_ws(
            "approval",
            {"session": session.key, "id": request_id, "tool": "write_file"},
        )
        return future

    pending_future = asyncio.run(pending_owner_request())
    store.update_status(loop.id, LoopStatus.RUNNING, attended=False)
    current_unattended = store.get(loop.id)
    assert current_unattended is not None
    assert is_unattended_session(main_key)
    assert is_unattended_session(task_key)
    assert profile_for_session(main_key).name == HEADLESS.name
    for session in worker_sessions:
        manager._arm_worker_approval_posture(state, session, current_unattended)
        assert session._trust
        assert not session._trust_reads
        assert session._unattended
        assert session.acp_mode == "bypassPermissions"
    assert pending_future.done()
    assert pending_future.result() == "cancelled"
