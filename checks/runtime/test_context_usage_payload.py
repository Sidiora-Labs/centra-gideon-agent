from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gideon.engine.agents.native.runtime import _ModelExchange, _TurnTotals
from gideon.integrations.llm.anthropic import _MessagesDecoder
from gideon.integrations.llm.events import AgentEvent, ContextUsage, EVENT_COMPLETE
from gideon.integrations.llm.openai import _ChatDecoder
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


def test_native_terminal_uses_last_model_call_not_turn_sum():
    runtime = SimpleNamespace(_last_context_pct=None)
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


def test_native_unmeasured_last_call_does_not_reuse_earlier_measurement():
    runtime = SimpleNamespace(_last_context_pct=None)
    totals = _TurnTotals()
    for usage in [ContextUsage(input_tokens=20), None]:
        exchange = _ModelExchange(runtime, totals)
        exchange.attempts = 1
        exchange.accept(AgentEvent(kind=EVENT_COMPLETE, context_usage=usage))
        totals.account(exchange)
    assert totals.finish("end_turn", None).context_usage is None


async def _events(items):
    for item in items:
        yield item


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage,expected",
    [
        (
            ContextUsage(
                input_tokens=40,
                total_input_tokens=55,
                cache_creation_tokens=5,
                cache_read_tokens=10,
                context_window_tokens=1000,
            ),
            {
                "input_tokens": 40,
                "cache_creation_tokens": 5,
                "cache_read_tokens": 10,
                "context_window_tokens": 1000,
                "total_input_tokens": 55,
            },
        ),
        (
            ContextUsage(context_window_tokens=8192),
            {
                "input_tokens": None,
                "cache_creation_tokens": None,
                "cache_read_tokens": None,
                "context_window_tokens": 8192,
            },
        ),
        (None, None),
    ],
)
async def test_chat_runner_broadcasts_optional_measured_last_call(
    tmp_path, usage, expected
):
    sessions = MagicMock(count=0)
    sessions.get_pid.return_value = None
    client = MagicMock()
    client.provider_id = "acp:test"
    client.context_usage_pct = MagicMock(return_value=12.34)
    client.stream = MagicMock(
        side_effect=lambda *_: _events(
            [
                AgentEvent(kind="text_chunk", text="answer"),
                AgentEvent(
                    kind=EVENT_COMPLETE,
                    stop_reason="end_turn",
                    input_tokens=50,
                    cache_read_tokens=10,
                    context_usage=usage,
                ),
            ]
        )
    )
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.check_context_usage = MagicMock()
    state = ConsoleState(sessions=sessions, start_time=0.0)
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.context_builder = None
    state.consolidator = None
    state._hook_store = None
    session = _ChatSession("context-usage-test")
    session._trust = True
    with patch("gideon.interfaces.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")
    payloads = [
        call.args[1]
        for call in state.broadcast_ws.call_args_list
        if call.args[0] == "context_usage"
    ]
    assert payloads == [
        {"session": session.key, "pct": 12.3, **({"usage": expected} if expected else {})}
    ]
