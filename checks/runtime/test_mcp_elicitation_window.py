import asyncio
import sys
import time
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp.types import ElicitRequestFormParams
from test_mcp_client import _ELICITATION_SERVER

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations import mcp_client
from gideon.interfaces.dashboard.state import ConsoleState


@asynccontextmanager
async def connected_console():
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=time.time())

    async def websocket(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        state.register_ws(socket)
        try:
            async for message in socket:
                pass
        finally:
            state.unregister_ws(socket)
        return socket

    app = web.Application()
    app.router.add_get("/ws", websocket)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as socket:
            yield state, socket


def question():
    return ElicitRequestFormParams(
        message="Choose a value",
        requestedSchema={"type": "object", "properties": {"value": {"type": "string"}}},
    )


@pytest.mark.parametrize("ceiling", [0.0, 0.001, 0.5, 120.0, 600.0])
def test_approval_window_never_exceeds_call_ceiling(monkeypatch, ceiling):
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", ceiling)
    window = mcp_client.approval_window_secs()
    assert 0 <= window <= ceiling
    if ceiling:
        assert window < ceiling
        assert window == pytest.approx(ceiling * 0.9)


@pytest.mark.asyncio
async def test_expiry_withdraws_card_and_rejects_late_accept(monkeypatch):
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", 0.1)
    async with connected_console() as (state, socket):
        started = asyncio.get_running_loop().time()
        pending = asyncio.create_task(
            state.request_mcp_elicitation("allowed", question())
        )
        card = await socket.receive_json(timeout=2)
        request_id = card["data"]["id"]
        assert card["type"] == "mcp_elicitation"
        result = await asyncio.wait_for(pending, timeout=2)
        elapsed = asyncio.get_running_loop().time() - started
        assert result.action == "cancel"
        assert elapsed >= mcp_client.approval_window_secs()
        assert elapsed < 1
        assert await socket.receive_json(timeout=2) == {
            "type": "mcp_elicitation_withdrawn",
            "data": {"id": request_id},
        }
        assert not state.resolve_mcp_elicitation(request_id, {"action": "accept"})
        assert not state._mcp_elicitation_futures
        assert not state._mcp_elicitation_deadlines


@pytest.mark.asyncio
async def test_on_time_response_is_preserved_and_withdrawn(monkeypatch):
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", 5.0)
    async with connected_console() as (state, socket):
        pending = asyncio.create_task(
            state.request_mcp_elicitation("allowed", question())
        )
        request_id = (await socket.receive_json(timeout=2))["data"]["id"]
        assert state.resolve_mcp_elicitation(
            request_id,
            {"action": "accept", "content": {"value": "chosen"}},
        )
        result = await pending
        assert result.action == "accept" and result.content == {"value": "chosen"}
        assert (await socket.receive_json(timeout=2))[
            "type"
        ] == "mcp_elicitation_withdrawn"
        assert not state.resolve_mcp_elicitation(request_id, {"action": "accept"})


@pytest.mark.asyncio
async def test_deadline_rejects_accept_before_timeout_callback_runs(monkeypatch):
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", 0.1)
    async with connected_console() as (state, socket):
        pending = asyncio.create_task(
            state.request_mcp_elicitation("allowed", question())
        )
        request_id = (await socket.receive_json(timeout=2))["data"]["id"]
        time.sleep(0.12)
        assert not state.resolve_mcp_elicitation(request_id, {"action": "accept"})
        assert (await pending).action == "cancel"
        assert (await socket.receive_json(timeout=2))[
            "type"
        ] == "mcp_elicitation_withdrawn"


@pytest.mark.asyncio
async def test_caller_cancellation_withdraws_card():
    async with connected_console() as (state, socket):
        pending = asyncio.create_task(
            state.request_mcp_elicitation("allowed", question())
        )
        request_id = (await socket.receive_json(timeout=2))["data"]["id"]
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await socket.receive_json(timeout=2) == {
            "type": "mcp_elicitation_withdrawn",
            "data": {"id": request_id},
        }
        assert not state._mcp_elicitation_futures
        assert not state._mcp_elicitation_deadlines


@pytest.mark.asyncio
async def test_granted_stdio_elicitation_returns_cancel_before_call_ceiling(
    tmp_path, monkeypatch
):
    server = tmp_path / "elicitation_server.py"
    server.write_text(_ELICITATION_SERVER)
    async with connected_console() as (state, socket):
        registry = mcp_client.McpClientRegistry(
            elicitation_handler=state.request_mcp_elicitation
        )
        registry.load_from_specs(
            {
                "allowed": {
                    "command": sys.executable,
                    "args": [str(server)],
                    "allowElicitation": True,
                }
            }
        )
        connection = registry.get("allowed")
        try:
            assert await connection.list_tools()
            monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", 1.0)
            pending = asyncio.create_task(connection.call_tool("ask", {}))
            request_id = (await socket.receive_json(timeout=2))["data"]["id"]
            assert await asyncio.wait_for(pending, timeout=3) == (True, "cancel")
            assert await socket.receive_json(timeout=2) == {
                "type": "mcp_elicitation_withdrawn",
                "data": {"id": request_id},
            }
            assert not state._mcp_elicitation_futures
        finally:
            await registry.shutdown_all()
