"""Actual callback and forwarding lifetimes close before the next SDK/native turn."""

import asyncio

import pytest
from test_native_connection_recovery import _answer, _http_streams, _provider
from test_short_model_reader_closure import retain

from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm import protocol_turn
from gideon.integrations.llm.events import EVENT_TEXT_CHUNK
from gideon.integrations.llm_helpers import stream_and_collect
from gideon.interfaces.dashboard.chat_utils import stream_slash_command


@pytest.mark.asyncio
async def test_collector_terminal_closes_retained_protocol_and_sdk_iterators(
    monkeypatch,
):
    wrappers = []
    original = protocol_turn.until_terminal

    def retained(*args, **kwargs):
        stream = original(*args, **kwargs)
        wrappers.append(stream)
        return stream

    monkeypatch.setattr(protocol_turn, "until_terminal", retained)
    async with _http_streams([_answer(), _answer()]) as (endpoint, requests, _, __):
        provider = _provider(endpoint)
        streams = retain(provider)
        await provider.start()
        try:
            assert await stream_and_collect(provider, "Finish") == "Finished"
            assert wrappers and all(stream.ag_frame is None for stream in wrappers)
            assert all(stream.ag_frame is None for stream in streams)
            assert (
                await asyncio.wait_for(stream_and_collect(provider, "Finish again"), 3)
                == "Finished"
            )
            assert len(requests) == 2
        finally:
            await provider.shutdown()


@pytest.mark.asyncio
async def test_collector_callback_error_closes_tcp_before_same_provider_recovers(
    monkeypatch,
):
    wrappers = []
    original = protocol_turn.until_terminal

    def retained(*args, **kwargs):
        stream = original(*args, **kwargs)
        wrappers.append(stream)
        return stream

    monkeypatch.setattr(protocol_turn, "until_terminal", retained)
    held = ([({"content": "Partial answer"}, None)], "hold")
    async with _http_streams([held, _answer()]) as (
        endpoint,
        requests,
        _,
        disconnected,
    ):
        provider = _provider(endpoint)
        streams = retain(provider)
        await provider.start()

        def consumer_failed(text):
            raise ValueError("The actual chunk consumer failed")

        try:
            with pytest.raises(ValueError, match="actual chunk consumer failed"):
                await asyncio.wait_for(
                    stream_and_collect(provider, "Read", on_chunk=consumer_failed), 5
                )
            await asyncio.wait_for(disconnected.wait(), 3)
            assert all(stream.ag_frame is None for stream in wrappers + streams)
            assert (
                await asyncio.wait_for(stream_and_collect(provider, "Recover"), 3)
                == "Finished"
            )
            assert len(requests) == 2
        finally:
            await provider.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["native-default-command", "slash-no-command-axis"])
async def test_outer_close_reaches_actual_native_sdk_turn_before_next_prompt(
    path, tmp_path
):
    held = ([({"content": "Partial answer"}, None)], "hold")
    async with _http_streams([held, _answer()]) as (
        endpoint,
        requests,
        _,
        disconnected,
    ):
        provider = _provider(endpoint)
        sdk_streams = []
        actual_complete = provider.complete

        def retaining_complete(*args, **kwargs):
            stream = actual_complete(*args, **kwargs)
            sdk_streams.append(stream)
            return stream

        provider.complete = retaining_complete
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="Reader closure", provider="native", model="local-wire"
            ),
            model_provider=provider,
            tool_providers=[],
            cwd=tmp_path,
            session_key="reader-default",
            max_turns=2,
        )
        await runtime.start()
        native_streams = retain(runtime)
        notices = []
        outer = (
            runtime.stream_command("/help")
            if path == "native-default-command"
            else stream_slash_command(
                runtime, "/help", prompt="Help me", notify=notices.append
            )
        )
        try:
            for _ in range(20):
                event = await asyncio.wait_for(anext(outer), 5)
                if event.kind == EVENT_TEXT_CHUNK:
                    break
            else:
                pytest.fail(
                    "Actual native runtime did not yield the partial SDK answer"
                )
            await asyncio.wait_for(outer.aclose(), 3)
            await asyncio.wait_for(disconnected.wait(), 3)
            assert native_streams and all(
                stream.ag_frame is None for stream in native_streams
            )
            assert sdk_streams and all(
                stream.ag_frame is None for stream in sdk_streams
            )
            assert (
                await asyncio.wait_for(stream_and_collect(runtime, "Now finish"), 5)
                == "Finished"
            )
            assert len(requests) == 2
            if path == "slash-no-command-axis":
                assert len(notices) == 1 and "plain message" in notices[0]
        finally:
            await outer.aclose()
            await runtime.shutdown()
