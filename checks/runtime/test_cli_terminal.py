"""Gateway event handling in the interactive terminal client."""

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.cli.terminal import GatewayClient, TerminalState
from gideon.interfaces.cli.terminal import TerminalApp
from gideon.interfaces.dashboard.chat_handlers import (
    api_chat_session_approve,
    api_chat_session_create,
    api_chat_session_detail,
    api_chat_session_resume,
    api_chat_sessions,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.interfaces.dashboard.ws import api_ws


def test_terminal_keeps_live_turn_and_approval_in_its_session():
    state = TerminalState(session="terminal-one", running=True)
    state.event({"type": "chat_chunk", "data": {"session": "terminal-other", "content": "wrong"}})
    state.event({"type": "chat_chunk", "data": {"session": "terminal-one", "content": "hello"}})
    state.event({"type": "chat_chunk", "data": {"session": "terminal-one", "content": " world"}})
    state.event({"type": "tool_call", "data": {"session": "terminal-one", "tool": "read_file", "purpose": "inspect"}})
    state.event({"type": "approval", "data": {"session": "terminal-one", "id": "permit-1", "tool": "write_file", "tool_input": "file.txt"}})
    assert state.pending and state.pending["id"] == "permit-1"
    state.event({"type": "approval_resolved", "data": {"id": "permit-1", "decision": "rejected"}})
    state.event({"type": "chat_done", "data": {"session": "terminal-one"}})
    assert state.pending is None
    assert state.running is False
    assert "Gideon: hello world" in state.lines
    assert "Tool: read_file inspect" in state.lines
    assert "Approval rejected" in state.lines
    assert not any("wrong" in line for line in state.lines)


def test_local_gateway_token_is_confined_to_the_requested_origin():
    client = GatewayClient("http://127.0.0.1:6777", token="token/with?characters")
    assert client.url_for("/api/chat?ws=1") == (
        "http://127.0.0.1:6777/api/chat?ws=1&token=token%2Fwith%3Fcharacters"
    )


@pytest.mark.asyncio
async def test_terminal_authenticated_session_ws_and_approval_journey(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    sessions = ConversationDirectory(AppConfig())
    state = ConsoleState(
        sessions=sessions,
        start_time=0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    server_app = web.Application(middlewares=[token_auth_middleware()])
    server_app["state"] = state
    server_app["allowed_origins"] = set()
    server_app.router.add_get("/api/ws", api_ws)
    server_app.router.add_get("/api/chat/sessions", api_chat_sessions)
    server_app.router.add_post("/api/chat/sessions", api_chat_session_create)
    server_app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    server_app.router.add_post("/api/chat/sessions/{session}/resume", api_chat_session_resume)
    server_app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)

    async with TestServer(server_app) as server:
        token = generate_token("terminal-journey")
        async with GatewayClient(str(server.make_url("")), token=token) as client:
            terminal = TerminalApp(client)
            await client.connect()
            reader = asyncio.create_task(terminal._read_events())
            try:
                await terminal._new_session("journey")
                assert terminal.state.session == "terminal-journey"
                listed = await client.request("GET", "/api/chat/sessions?all=1")
                assert any(row["key"] == "terminal-journey" for row in listed["items"])
                await terminal._open_session("terminal-journey")
                request_id = "permit-journey"
                future = asyncio.get_running_loop().create_future()
                state._sessions[terminal.state.session]._approval_futures[request_id] = future
                state.broadcast_ws(
                    "approval",
                    {"session": terminal.state.session, "id": request_id, "tool": "write_file", "tool_input": "file.txt"},
                )
                async with asyncio.timeout(3):
                    while terminal.state.pending is None:
                        await asyncio.sleep(0.01)
                await terminal._key(ord("n"))
                assert await future == "rejected"
                assert any("Approval rejected" in line for line in terminal.state.lines)
            finally:
                terminal.alive = False
                reader.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await reader
