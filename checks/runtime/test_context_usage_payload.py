import json

from aiohttp import ClientSession, web
import pytest

from gideon.engine.agents.native.runtime import _ModelExchange, _TurnTotals
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.engine.hooks import ScriptHookStore
from gideon.engine.session import ConversationDirectory
from gideon.core.config import AppConfig
from gideon.integrations.llm.anthropic import _MessagesDecoder
from gideon.integrations.llm.events import AgentEvent, ContextUsage, EVENT_COMPLETE
from gideon.integrations.llm.openai import _ChatDecoder
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.protocol_turn import TurnUsage
from gideon.interfaces.dashboard.chat_runner import run_chat
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def test_anthropic_decoder_preserves_reported_cache_buckets():
    decoder = _MessagesDecoder()
    decoder.feed(
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 90,
                    "cache_creation_input_tokens": 30,
                    "cache_read_input_tokens": 180,
                }
            },
        }
    )
    usage = decoder.usage.terminal(15, context_window_tokens=2000).context_usage
    assert usage is not None
    assert usage.as_payload() == {
        "input_tokens": 90,
        "cache_creation_tokens": 30,
        "cache_read_tokens": 180,
        "context_window_tokens": 2000,
        "total_input_tokens": 300,
    }


def test_anthropic_missing_cache_fields_remain_unknown():
    decoder = _MessagesDecoder()
    decoder.feed(
        {"type": "message_start", "message": {"usage": {"input_tokens": 0}}}
    )
    usage = decoder.usage.terminal(None).context_usage
    assert usage is not None
    assert usage.input_tokens == 0
    assert usage.cache_creation_tokens is None
    assert usage.cache_read_tokens is None
    assert usage.total_input_tokens is None


def test_openai_cache_details_define_exclusive_input_and_zero_cache():
    decoder = _ChatDecoder()
    decoder.feed(
        {
            "usage": {
                "prompt_tokens": 400,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        }
    )
    usage = decoder.usage.terminal(2).context_usage
    assert usage is not None
    assert usage.input_tokens == 400
    assert usage.cache_read_tokens == 0
    assert usage.cache_creation_tokens is None
    assert usage.total_input_tokens == 400


def test_openai_missing_cache_details_do_not_claim_uncached_input():
    decoder = _ChatDecoder()
    decoder.feed({"usage": {"prompt_tokens": 400}})
    assert decoder.usage.input_tokens == 400
    assert decoder.usage.terminal(2, context_window_tokens=8000).context_usage == (
        ContextUsage(total_input_tokens=400, context_window_tokens=8000)
    )
    assert decoder.usage.terminal(2).context_usage == ContextUsage(
        total_input_tokens=400
    )
    assert TurnUsage().terminal(2, context_window_tokens=8000).context_usage == (
        ContextUsage(context_window_tokens=8000)
    )
    assert TurnUsage().terminal(2).context_usage is None


@pytest.mark.parametrize("cached", [-1, 500, True, "20"])
def test_openai_invalid_cache_details_do_not_become_measured_buckets(cached):
    decoder = _ChatDecoder()
    decoder.feed(
        {
            "usage": {
                "prompt_tokens": 400,
                "prompt_tokens_details": {"cached_tokens": cached},
            }
        }
    )
    assert decoder.usage.terminal(1).context_usage == ContextUsage(
        total_input_tokens=400
    )


@pytest.mark.asyncio
async def test_native_terminal_uses_last_model_call_not_turn_sum():
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="usage"),
        model_provider=OpenAIProvider(model="local-wire", credential=Credential("local", "api_key", "local")),
    )
    totals = _TurnTotals()
    for input_tokens, cached, window in [(100, 0, 2000), (50, 30, 4000)]:
        exchange = _ModelExchange(runtime, totals)
        exchange.attempts = 1
        exchange.accept(
            AgentEvent(
                kind=EVENT_COMPLETE,
                input_tokens=input_tokens,
                cache_read_tokens=cached,
                context_usage_pct=(input_tokens + cached) / window * 100,
                context_usage=ContextUsage(
                    input_tokens=input_tokens,
                    total_input_tokens=input_tokens + cached,
                    cache_read_tokens=cached,
                    context_window_tokens=window,
                ),
            )
        )
        totals.account(exchange)
    terminal = totals.finish("end_turn", runtime._last_context_pct)
    assert terminal.input_tokens == 150
    assert terminal.cache_read_tokens == 30
    assert terminal.context_usage == ContextUsage(
        input_tokens=50,
        total_input_tokens=80,
        cache_read_tokens=30,
        context_window_tokens=4000,
    )
    assert terminal.context_usage_pct == 2
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_native_unmeasured_last_call_does_not_reuse_earlier_measurement():
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="usage"),
        model_provider=OpenAIProvider(model="local-wire", credential=Credential("local", "api_key", "local")),
    )
    totals = _TurnTotals()
    for usage in [ContextUsage(input_tokens=20), None]:
        exchange = _ModelExchange(runtime, totals)
        exchange.attempts = 1
        exchange.accept(AgentEvent(kind=EVENT_COMPLETE, context_usage=usage))
        totals.account(exchange)
    assert totals.finish("end_turn", None).context_usage is None
    await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire_usage,expected",
    [
        (
            {"prompt_tokens": 50, "prompt_tokens_details": {"cached_tokens": 10},
             "completion_tokens": 5},
            {
                "input_tokens": 40,
                "cache_creation_tokens": None,
                "cache_read_tokens": 10,
                "context_window_tokens": 1000,
                "total_input_tokens": 50,
            },
        ),
        (
            None,
            {
                "input_tokens": None,
                "cache_creation_tokens": None,
                "cache_read_tokens": None,
                "context_window_tokens": 1000,
            },
        ),
    ],
)
async def test_chat_runner_broadcasts_optional_measured_last_call(
    tmp_path, monkeypatch, wire_usage, expected
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    state = None

    async def inference(_request):
        frame = {"id": "usage-turn", "object": "chat.completion.chunk",
                 "created": 1, "model": "local-wire", "choices": [
                     {"index": 0, "delta": {"content": "answer"}, "finish_reason": "stop"}]}
        if wire_usage is not None:
            frame["usage"] = wire_usage
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
    app.router.add_post("/v1/chat/completions", inference)
    app.router.add_get("/ws", websocket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    def provider_factory(key, **_options):
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="usage", model="local-wire"),
            model_provider=OpenAIProvider(
                model="local-wire", credential=Credential("local", "api_key", "local"),
                base_url=f"http://127.0.0.1:{port}/v1",
                extra_options={"context_window": 1000},
            ),
            session_key=key,
        )

    cfg = AppConfig.load()
    cfg.session.pool_size = 0
    sessions = ConversationDirectory(cfg, provider_factory)
    state = ConsoleState(sessions=sessions, start_time=0.0)
    state._hook_store = ScriptHookStore(home)
    session = _ChatSession("context-usage-test")
    session._trust = True
    try:
        async with ClientSession() as client:
            async with client.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                await run_chat(state, session, "hello")
                frames = []
                while True:
                    try:
                        message = await ws.receive(timeout=0.25)
                    except TimeoutError:
                        break
                    if message.type != web.WSMsgType.TEXT:
                        break
                    frames.append(json.loads(message.data))
        payloads = [frame["data"] for frame in frames if frame["type"] == "context_usage"]
        assert len(payloads) == 1
        assert payloads[0]["session"] == session.key
        assert payloads[0]["usage"] == expected
    finally:
        await sessions.close_all()
        await runner.cleanup()
