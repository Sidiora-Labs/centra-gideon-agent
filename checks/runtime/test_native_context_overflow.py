from __future__ import annotations

import json

import pytest

from gideon.cognition.context_compaction import total_chars
from gideon.engine.agents.native.runtime import (
    _MAX_INFERENCE_RECOVERIES_PER_TURN,
    NativeAgentRuntime,
    _ModelExchange,
    _TurnTotals,
)
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from gideon.integrations.llm.prompt_cache import (
    CACHE_HINT_KEY,
    PromptCache,
    mark_cacheable_prefix,
)
from gideon.integrations.llm.scripted import ScriptedProvider
from gideon.security.guardrails.failure import CircuitOpenError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    script = tmp_path / "conversation.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [{"expect_prompt": "latest request", "text": "Completed"}],
            }
        )
    )
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="Recovery", provider="native"),
        model_provider=ScriptedProvider(),
        cwd=tmp_path,
    )
    runtime._messages = [
        {"role": "system", "content": "Preserve the latest request."},
        *[
            {
                "role": "user" if index % 2 else "assistant",
                "content": f"Historical passage {index}: " + "detail " * 800,
            }
            for index in range(20)
        ],
        {"role": "user", "content": "latest request"},
    ]
    runtime._last_context_pct = 12.0
    return runtime


async def test_overflow_forces_compaction_and_rebuilds_cache_boundary(runtime):
    runtime._compaction_saves = [0.0, 0.0]
    exchange = _ModelExchange(runtime, _TurnTotals())
    original = mark_cacheable_prefix(runtime._messages, PromptCache.EXPLICIT)
    size = total_chars(original)

    rebuilt = await exchange._retry_messages(
        RuntimeError("context_length_exceeded"), original, PromptCache.EXPLICIT
    )

    assert total_chars(rebuilt) < size
    assert total_chars(original) == size
    assert rebuilt[-1]["content"] == "latest request"
    assert rebuilt[-1][CACHE_HINT_KEY] == {"generation": 1}
    assert CACHE_HINT_KEY not in runtime._messages[-1]
    assert runtime._last_context_pct < 12.0
    assert exchange.retried and exchange.totals.recoveries == 1
    assert not any(message.get("_volatile") for message in rebuilt)
    events = [event async for event in runtime._model.complete(rebuilt)]
    assert [event.text for event in events if event.kind == EVENT_TEXT_CHUNK] == [
        "Completed"
    ]
    assert events[-1].kind == EVENT_COMPLETE
    assert runtime._model.last_complete_call["message_count"] == len(rebuilt)


@pytest.mark.parametrize("first_overflow", [False, True])
async def test_overflow_and_transient_share_the_retry_latch(runtime, first_overflow):
    exchange = _ModelExchange(runtime, _TurnTotals())
    overflow = RuntimeError("maximum context length exceeded")
    timeout = TimeoutError("timed out")
    first, second = (overflow, timeout) if first_overflow else (timeout, overflow)
    messages = await exchange._retry_messages(
        first, runtime._messages, PromptCache.NONE
    )
    after_first = runtime._messages
    generation = runtime._cache_generation

    with pytest.raises(type(second)) as caught:
        await exchange._retry_messages(second, messages, PromptCache.NONE)

    assert caught.value is second
    assert runtime._messages is after_first
    assert runtime._cache_generation == generation
    assert exchange.totals.recoveries == 1


async def test_turn_recovery_ceiling_blocks_overflow_compaction(runtime):
    totals = _TurnTotals(recoveries=_MAX_INFERENCE_RECOVERIES_PER_TURN)
    exchange = _ModelExchange(runtime, totals)
    original = runtime._messages
    error = RuntimeError("prompt is too long")
    with pytest.raises(RuntimeError) as caught:
        await exchange._retry_messages(error, original, PromptCache.NONE)
    assert caught.value is error
    assert runtime._messages is original
    assert runtime._cache_generation == 0


@pytest.mark.parametrize(
    "messages", [[], [{"role": "user", "content": "latest request"}]]
)
async def test_no_space_propagates_original_error_without_consuming_retry(
    runtime, messages
):
    runtime._messages = messages
    exchange = _ModelExchange(runtime, _TurnTotals())
    error = RuntimeError("context_length_exceeded")
    with pytest.raises(RuntimeError) as caught:
        await exchange._retry_messages(error, messages, PromptCache.EXPLICIT)
    assert caught.value is error
    assert runtime._messages is messages
    assert runtime._cache_generation == 0
    assert not exchange.retried
    assert exchange.totals.recoveries == 0


async def test_visible_output_cannot_trigger_overflow_replay(runtime):
    exchange = _ModelExchange(runtime, _TurnTotals())
    exchange.accept(AgentEvent(kind=EVENT_TEXT_CHUNK, text="Already visible"))
    error = RuntimeError("context_length_exceeded")
    with pytest.raises(RuntimeError) as caught:
        await exchange._retry_messages(error, runtime._messages, PromptCache.NONE)
    assert caught.value is error
    assert runtime._cache_generation == 0


async def test_nonretryable_failure_remains_nonretryable(runtime):
    exchange = _ModelExchange(runtime, _TurnTotals())
    error = CircuitOpenError("context_length_exceeded", 30.0)
    with pytest.raises(CircuitOpenError) as caught:
        await exchange._retry_messages(error, runtime._messages, PromptCache.NONE)
    assert caught.value is error
    assert runtime._cache_generation == 0


@pytest.mark.parametrize("first_overflow", [False, True])
async def test_overflow_retry_latch_survives_new_exchange(runtime, first_overflow):
    totals = _TurnTotals()
    first_exchange = _ModelExchange(runtime, totals)
    overflow = RuntimeError("context_length_exceeded")
    timeout = TimeoutError("timed out")
    first, second = (overflow, timeout) if first_overflow else (timeout, overflow)
    messages = await first_exchange._retry_messages(
        first, runtime._messages, PromptCache.NONE
    )
    generation = runtime._cache_generation
    next_exchange = _ModelExchange(runtime, totals)
    with pytest.raises(type(second)) as caught:
        await next_exchange._retry_messages(second, messages, PromptCache.NONE)
    assert caught.value is second
    assert totals.recoveries == 1
    assert runtime._cache_generation == generation


async def test_overflow_cannot_recover_twice_across_exchanges(runtime):
    totals = _TurnTotals()
    first_exchange = _ModelExchange(runtime, totals)
    messages = await first_exchange._retry_messages(
        RuntimeError("context_length_exceeded"), runtime._messages, PromptCache.NONE
    )
    error = RuntimeError("prompt is too long")
    next_exchange = _ModelExchange(runtime, totals)
    with pytest.raises(RuntimeError) as caught:
        await next_exchange._retry_messages(error, messages, PromptCache.NONE)
    assert caught.value is error
    assert totals.recoveries == 1
    assert runtime._cache_generation == 1
