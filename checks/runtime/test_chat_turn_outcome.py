import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_handlers import (
    api_chat_session_detail,
    api_chat_session_stop,
)
from gideon.interfaces.dashboard.chat_regenerate import (
    api_chat_session_edit_resend,
    api_chat_session_regenerate,
)
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def _state(tmp_path):
    return ConsoleState(
        sessions=ConversationDirectory(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "stopped", "error"])
async def test_session_detail_restores_persisted_terminal_outcome(tmp_path, outcome):
    state = _state(tmp_path)
    session = _ChatSession(f"outcome-{outcome}")
    state._sessions[session.key] = session
    session.append("user", "Run the requested task.", "msg msg-u")
    session.append("assistant", "The result is ready.", "msg msg-a", meta={"last_turn_outcome": outcome})
    save_session_to_history(state, session, force=True)
    state._sessions.clear()

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/chat/sessions/{session.key}")
        detail = await response.json()

    assert response.status == 200
    assert detail["last_turn_outcome"] == outcome


@pytest.mark.asyncio
async def test_stop_and_rewrite_refusals_preserve_server_transcript(tmp_path):
    state = _state(tmp_path)
    session = _ChatSession("transactional-actions")
    state._sessions[session.key] = session
    session.append("system", "Keep this row unchanged", "msg msg-s")
    before = [dict(message) for message in session.messages]

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    async with TestClient(TestServer(app)) as client:
        stop_response = await client.post(f"/api/chat/sessions/{session.key}/stop")
        regenerate_response = await client.post(f"/api/chat/sessions/{session.key}/regenerate")
        edit_response = await client.post(
            f"/api/chat/sessions/{session.key}/edit-resend",
            json={"content": "Edited prompt", "ts": before[0]["ts"], "index": 0},
        )
        stop_payload = await stop_response.json()
        assert stop_payload["stopped"] is False
        assert stop_payload["snapshot"]["key"] == session.key
        assert stop_payload["snapshot"]["last_turn_outcome"] is None
        assert regenerate_response.status == 400
        assert edit_response.status == 400
        assert session.messages == before


@pytest.mark.asyncio
async def test_detail_finds_a_persisted_stop_event_after_process_restore(tmp_path):
    state = _state(tmp_path)
    session = _ChatSession("restored-stop")
    state._sessions[session.key] = session
    stop_event = {"kind": "stop_event", "state": "stopped", "outcome": "soft"}
    session.append("user", "Stop this task.", "msg msg-u")
    session.append("system", json.dumps(stop_event), json.dumps(stop_event))
    save_session_to_history(state, session, force=True)
    state._sessions.clear()

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/chat/sessions/{session.key}")
        detail = await response.json()

    assert response.status == 200
    assert detail["last_turn_outcome"] == "stopped"
