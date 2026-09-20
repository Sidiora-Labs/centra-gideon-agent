from __future__ import annotations

import asyncio

from gideon.cognition.context_compaction import total_chars
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.events import EVENT_COMPACTION_STATUS
from gideon.interfaces.dashboard.chat_utils import stream_slash_command


class _Model:
    supports_tools = False


def _runtime(messages: list[dict]) -> NativeAgentRuntime:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="test", model="test"),
        model_provider=_Model(),
    )
    runtime._messages = messages
    return runtime


async def _run_compact(runtime: NativeAgentRuntime) -> dict:
    events = [
        event
        async for event in stream_slash_command(
            runtime, "/compact", prompt="unused", notify=lambda _: None
        )
    ]
    assert len(events) == 1
    assert events[0].kind == EVENT_COMPACTION_STATUS
    return await runtime.wait_for_compaction()


def test_native_compact_reports_measured_before_and_after():
    messages = [{"role": "system", "content": "system"}]
    messages.extend(
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 1000}
        for index in range(20)
    )
    runtime = _runtime(messages)

    result = asyncio.run(_run_compact(runtime))

    assert runtime.compacts_in_process is True
    assert result["type"] == "completed"
    assert result["before"] > result["after"] == total_chars(runtime._messages)
    assert f"{result['before']:,}" in result["summary"]
    assert f"{result['after']:,}" in result["summary"]


def test_native_compact_short_chat_is_noop():
    runtime = _runtime([{"role": "user", "content": "hello"}])

    result = asyncio.run(_run_compact(runtime))

    assert result == {
        "type": "noop",
        "before": 5,
        "after": 5,
        "summary": "5 → 5 characters",
    }
    assert runtime._messages == [{"role": "user", "content": "hello"}]
