import asyncio
import json

from aiohttp import ClientSession, web
import pytest

from gideon.core.config import AppConfig
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.engine.agents.native.runtime import NativeAgentRuntime, _PreparedCall
from gideon.engine.agents.native.tools import InProcessMcpToolProvider, format_tool_result
from gideon.engine.hooks import ScriptHookStore
from gideon.engine.session import ConversationDirectory
from gideon.integrations import mcp_core
from gideon.integrations.computer_use import enable_state, tools as computer_tools
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.events import AgentEvent, EVENT_TOOL_CALL, EVENT_TOOL_RESULT
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.tool_providers.registry import create_computer_use_provider
from gideon.interfaces.dashboard.chat_runner import _tool_result_payload, run_chat
from gideon.interfaces.dashboard.handlers.computer_use import api_computer_use_dispatch
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.mark.asyncio
async def test_real_computer_use_refusal_reaches_native_tool_event(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_COMPUTER_USE_ENABLE_FILE", str(tmp_path / "absent-enable.json"))
    enable_state.reset_enable_state()
    app = web.Application()
    app.router.add_post("/api/computer-use/dispatch", api_computer_use_dispatch)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    monkeypatch.setenv("GIDEON_PORT", str(site._server.sockets[0].getsockname()[1]))

    try:
        provider = create_computer_use_provider()
        result = await provider.invoke("computer_click", {"snapshot_id": "missing", "element_index": 0})
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_COMPUTER_USE_DISABLED"
        assert "desktop computer use is OFF" in result.agent_error.what

        wire = await asyncio.to_thread(
            mcp_core._post, "/api/computer-use/dispatch",
            {"tool": "computer_click", "params": {"snapshot_id": "missing", "element_index": 0}},
        )
        assert wire["error"]["code"] == "computer_use_refused"
        assert wire["error"]["agent_code"] == result.agent_error.code
        assert wire["error"]["what"] == result.agent_error.what
        mcp_text = await asyncio.to_thread(
            computer_tools._call_tool, "computer_click", {"snapshot_id": "missing", "element_index": 0}
        )
        assert isinstance(mcp_text, str)
        assert str(mcp_text) == computer_tools._render_error(wire["error"])
        assert mcp_text.agent_error.to_dict() == result.agent_error.to_dict()

        call = AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="computer-1", title="computer_click")
        prepared = _PreparedCall(
            call=call, tool_name="computer_click", args={}, card=call,
            reservations=(), bkey="computer_click",
        )
        event = prepared.result_event(
            format_tool_result(result), NativeAgentRuntime._result_metadata(result)
        )
        assert event.kind == EVENT_TOOL_RESULT
        assert event.tool_meta["ok"] is False
        assert event.tool_meta["agent_error"] == result.agent_error.to_dict()
        assert event.tool_meta["agent_error"]["what"] == result.agent_error.what
        payload = _tool_result_payload("dashboard_computer-test", event, event.tool_output)
        assert payload["session"] == "dashboard_computer-test"
        assert payload["tool_call_id"] == "computer-1"
        assert payload["ok"] is False
        assert payload["agent_error"] == result.agent_error.to_dict()
    finally:
        enable_state.reset_enable_state()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_real_computer_use_refusal_reaches_chat_websocket(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_COMPUTER_USE_ENABLE_FILE", str(home / "absent-enable.json"))
    enable_state.reset_enable_state()
    state = None
    requests = []

    async def inference(request):
        requests.append(await request.json())
        if len(requests) == 1:
            delta = {"tool_calls": [{"index": 0, "id": "computer-real-1",
                     "type": "function", "function": {"name": "computer_click",
                     "arguments": json.dumps({"snapshot_id": "missing", "element_index": 0})}}]}
            reason = "tool_calls"
        else:
            delta = {"content": "Denied action recorded"}
            reason = "stop"
        frame = {"id": f"turn-{len(requests)}", "object": "chat.completion.chunk",
                 "created": 1, "model": "local-wire", "choices": [
                     {"index": 0, "delta": delta, "finish_reason": reason}],
                 "usage": {"prompt_tokens": 40, "prompt_tokens_details": {"cached_tokens": 0},
                           "completion_tokens": 4}}
        return web.Response(text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n",
                            content_type="text/event-stream")

    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.register_ws(ws)
        try:
            async for _ in ws:
                pass
        finally:
            state.unregister_ws(ws)
        return ws

    app = web.Application()
    app.router.add_post("/api/computer-use/dispatch", api_computer_use_dispatch)
    app.router.add_post("/v1/chat/completions", inference)
    app.router.add_get("/ws", websocket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("GIDEON_PORT", str(port))
    endpoint = f"http://127.0.0.1:{port}/v1"

    def provider_factory(key, **_options):
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="Local wire", model="local-wire"),
            model_provider=OpenAIProvider(
                model="local-wire", credential=Credential("local", "api_key", "local"),
                base_url=endpoint,
            ),
            tool_providers=[create_computer_use_provider()], session_key=key,
            max_turns=2,
        )

    cfg = AppConfig.load()
    cfg.session.pool_size = 0
    directory = ConversationDirectory(cfg, provider_factory)
    state = ConsoleState(sessions=directory, start_time=0)
    state._hook_store = ScriptHookStore(home)
    session = _ChatSession("computer-real")
    session._trust = True
    try:
        async with ClientSession() as client:
            async with client.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                await run_chat(state, session, "Click the desktop element")
                frames = []
                while True:
                    try:
                        frame = await ws.receive(timeout=0.25)
                    except TimeoutError:
                        break
                    if frame.type != web.WSMsgType.TEXT:
                        break
                    frames.append(json.loads(frame.data))
        results = [frame["data"] for frame in frames if frame["type"] == "tool_result"]
        assert len(requests) >= 2, (requests, frames, session.messages)
        assert len(results) == 1, (frames, session.messages)
        assert results[0]["tool_call_id"] == "computer-real-1"
        assert results[0]["ok"] is False
        assert results[0]["agent_error"]["code"] == "ERR_COMPUTER_USE_DISABLED"
        assert "desktop computer use is OFF" in results[0]["agent_error"]["what"]
        stored = next(message for message in session.messages if message["role"] == "tool")
        assert stored["meta"]["agent_error"] == results[0]["agent_error"]
    finally:
        await directory.close_all()
        enable_state.reset_enable_state()
        await runner.cleanup()


@pytest.mark.parametrize("wire_error", [
    "transport error",
    {"agent_code": 42, "what": "blocked", "why": "policy", "fix": "request access"},
    {"agent_code": "ERR_OTHER", "what": "blocked", "why": "policy", "fix": "request access"},
    {"agent_code": "ERR_COMPUTER_USE_DISABLED", "what": 42, "why": "policy", "fix": "request access"},
    {"agent_code": "ERR_COMPUTER_USE_DISABLED", "what": "blocked", "why": "policy", "fix": "request access", "suggestions": "retry"},
    {"agent_code": "ERR_COMPUTER_USE_DISABLED", "what": "blocked", "why": "policy", "fix": "request access", "suggestions": [42]},
])
def test_untrusted_wire_error_cannot_become_policy_notice(wire_error):
    assert computer_tools._agent_error_from_wire(wire_error) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", [
    ("/api/computer-use/dispatch", "not-json"),
    ("/api/computer-use/dispatch", "[]"),
    ("/api/unrelated-tool", '{"error":"unrelated failure"}'),
])
async def test_http_error_fallback_keeps_unstructured_tools_as_plain_text(
    path, body, monkeypatch
):
    async def failure(_request):
        return web.Response(status=403, text=body)

    app = web.Application()
    app.router.add_post(path, failure)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    monkeypatch.setenv("GIDEON_PORT", str(site._server.sockets[0].getsockname()[1]))
    try:
        response = await asyncio.to_thread(mcp_core._post, path, {"tool": "computer_click"})
        assert response["error"].startswith("HTTP Error 403")
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_unstructured_computer_error_does_not_gain_agent_policy_metadata(monkeypatch):
    async def legacy_failure(_request):
        return web.json_response(
            {"error": {"code": "legacy_error", "what": "action refused",
                       "why": "legacy gateway", "fix": "retry"}}, status=403
        )

    app = web.Application()
    app.router.add_post("/api/computer-use/dispatch", legacy_failure)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    monkeypatch.setenv("GIDEON_PORT", str(site._server.sockets[0].getsockname()[1]))
    try:
        text = await asyncio.to_thread(computer_tools._call_tool, "computer_click", {})
        assert type(text) is str
        assert "action refused" in text
        assert not hasattr(text, "agent_error")
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_native_provider_keeps_success_and_other_mcp_modules_unchanged(monkeypatch):
    async def successful_dispatch(_request):
        return web.json_response({"result": "Desktop action completed"})

    app = web.Application()
    app.router.add_post("/api/computer-use/dispatch", successful_dispatch)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    monkeypatch.setenv("GIDEON_PORT", str(site._server.sockets[0].getsockname()[1]))
    try:
        computer = await create_computer_use_provider().invoke("computer_list_apps", {})
        assert computer.success is True
        assert computer.output == "Desktop action completed"
        assert computer.agent_error is None

        core = InProcessMcpToolProvider(module="gideon.integrations.mcp_core")
        legacy = await core.invoke("skill_search", {})
        assert legacy.success is True
        assert "query is required" in legacy.output
        assert legacy.agent_error is None
    finally:
        await runner.cleanup()
