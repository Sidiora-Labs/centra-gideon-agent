"""Real HTTP stream disconnects through the SDK and native tool loop."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_RESULT,
)
from gideon.integrations.llm.openai import OpenAIProvider


@asynccontextmanager
async def _http_streams(responses):
    requests = []
    connections = set()
    tasks = set()
    started = asyncio.Event()
    disconnected = asyncio.Event()

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        connections.add(writer)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            headers = dict(
                line.split(":", 1)
                for line in header.decode().split("\r\n")[1:]
                if ":" in line
            )
            size = int(
                next(
                    value
                    for key, value in headers.items()
                    if key.lower() == "content-length"
                )
            )
            request = json.loads(await reader.readexactly(size))
            index = len(requests)
            requests.append(request)
            frames, ending = responses[index]
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            )
            for delta, reason in frames:
                chunk = {
                    "id": f"completion-{index}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "local-wire",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                }
                data = ("data: " + json.dumps(chunk) + "\n\n").encode()
                writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            if ending == "complete":
                data = b"data: [DONE]\n\n"
                writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n")
            await writer.drain()
            started.set()
            if ending == "hold":
                await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            connections.discard(writer)
            tasks.discard(task)
            disconnected.set()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1", requests, started, disconnected
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(connections):
            writer.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _dropped():
    return [({"role": "assistant"}, None)], "drop"


def _read(number):
    call = {
        "index": 0,
        "id": f"read-{number}",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": json.dumps({"path": f"note-{number}.txt"}),
        },
    }
    return [({"tool_calls": [call]}, "tool_calls")], "complete"


def _answer():
    return [({"content": "Finished"}, None), ({}, "stop")], "complete"


def _provider(endpoint):
    return OpenAIProvider(
        model="local-wire",
        credential=Credential("local", "api_key", "local-transport-only"),
        base_url=endpoint,
    )


async def _runtime(tmp_path, model, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for number in range(1, 4):
        (workspace / f"note-{number}.txt").write_text(f"Stored observation {number}")
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="Transport recovery", provider="native", model="local-wire"
        ),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(workspace, sandbox_mode="none")],
        cwd=workspace,
        max_turns=8,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    return runtime, home


@pytest.mark.asyncio
async def test_each_exchange_recovers_without_replaying_completed_tools(
    tmp_path, monkeypatch
):
    responses = [
        _dropped(),
        _read(1),
        _read(2),
        _dropped(),
        _read(3),
        _dropped(),
        _answer(),
    ]
    async with _http_streams(responses) as (endpoint, requests, _, __):
        model = _provider(endpoint)
        runtime, home = await _runtime(tmp_path, model, monkeypatch)
        try:
            events = [
                event async for event in runtime.stream("Read the notes and finish")
            ]
        finally:
            await model.shutdown()
    assert len(requests) == 7
    results = [event for event in events if event.kind == EVENT_TOOL_RESULT]
    assert [event.tool_call_id for event in results] == ["read-1", "read-2", "read-3"]
    assert all(
        f"Stored observation {index}" in str(event.tool_output)
        for index, event in enumerate(results, 1)
    )
    assert (
        "".join(event.text for event in events if event.kind == EVENT_TEXT_CHUNK)
        == "Finished"
    )
    assert events[-1].kind == EVENT_COMPLETE
    assert events[-1].tool_call_count == 3
    for attempt in (0, 3, 5):
        assert requests[attempt]["messages"] == requests[attempt + 1]["messages"]
    assert [
        message["tool_call_id"]
        for message in requests[-1]["messages"]
        if message["role"] == "tool"
    ] == ["read-1", "read-2", "read-3"]
    audit = [
        json.loads(line)
        for line in (home / "model_calls.jsonl").read_text().splitlines()
    ]
    assert [(row["attempt"], row["passed"]) for row in audit] == [
        (1, False),
        (2, True),
    ] * 3


@pytest.mark.asyncio
async def test_turn_recovery_budget_stops_repeated_disconnections(
    tmp_path, monkeypatch
):
    responses = [
        _dropped(),
        _read(1),
        _dropped(),
        _read(2),
        _dropped(),
        _read(3),
        _dropped(),
    ]
    async with _http_streams(responses) as (endpoint, requests, _, __):
        model = _provider(endpoint)
        runtime, _ = await _runtime(tmp_path, model, monkeypatch)
        try:
            with pytest.raises(model._openai_module.APIConnectionError):
                _ = [event async for event in runtime.stream("Read three notes")]
        finally:
            await model.shutdown()
    assert len(requests) == 7
    assert [
        message["tool_call_id"]
        for message in runtime._messages
        if message["role"] == "tool"
    ] == ["read-1", "read-2", "read-3"]


@pytest.mark.asyncio
async def test_same_exchange_still_only_retries_once(tmp_path, monkeypatch):
    async with _http_streams([_dropped(), _dropped()]) as (endpoint, requests, _, __):
        model = _provider(endpoint)
        runtime, _ = await _runtime(tmp_path, model, monkeypatch)
        try:
            with pytest.raises(model._openai_module.APIConnectionError):
                _ = [event async for event in runtime.stream("Answer")]
        finally:
            await model.shutdown()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_partial_answer_is_not_replayed_after_connection_failure(
    tmp_path, monkeypatch
):
    response = [({"content": "Already visible"}, None)], "drop"
    async with _http_streams([response]) as (endpoint, requests, _, __):
        model = _provider(endpoint)
        runtime, _ = await _runtime(tmp_path, model, monkeypatch)
        received = []
        try:
            with pytest.raises(model._openai_module.APIConnectionError):
                async for event in runtime.stream("Answer"):
                    received.append(event)
        finally:
            await model.shutdown()
    assert len(requests) == 1
    assert [event.text for event in received if event.kind == EVENT_TEXT_CHUNK] == [
        "Already visible"
    ]


@pytest.mark.asyncio
async def test_cancel_closes_active_http_response_without_retry():
    response = [({"role": "assistant"}, None)], "hold"
    async with _http_streams([response]) as (endpoint, requests, started, disconnected):
        model = _provider(endpoint)

        async def consume():
            return [
                event
                async for event in model.complete([{"role": "user", "content": "Wait"}])
            ]

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            await asyncio.wait_for(disconnected.wait(), timeout=5)
            assert len(requests) == 1
        finally:
            await model.shutdown()
