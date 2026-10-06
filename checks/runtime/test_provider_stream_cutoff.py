"""Protocol EOF is qualified against local HTTP streams through the real adapters."""

from __future__ import annotations

import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web

from gideon.integrations.llm.anthropic import AnthropicProvider
from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
)
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm_helpers import humanize_provider_error, stream_and_collect
from gideon.security.guardrails.breaker import CircuitBreaker
from gideon.security.guardrails.budgets import SpendMeter
from gideon.security.guardrails.failure import AnswerCutOff, answer_cut_off
from gideon.security.guardrails.model_call import ModelCallGuard


def frames_for(protocol: str, terminal: bool) -> list[dict]:
    if protocol == "openai":

        def frame(delta, reason=None):
            return {
                "id": "chat-local",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "local-test",
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }

        rows = [
            frame({"role": "assistant", "content": "Partial reply"}),
            frame(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-local",
                            "type": "function",
                            "function": {"name": "write_file", "arguments": '{"path":'},
                        }
                    ]
                }
            ),
        ]
        if terminal:
            rows += [
                frame(
                    {"tool_calls": [{"index": 0, "function": {"arguments": '"a.md"}'}}]}
                ),
                frame({}, "tool_calls"),
                {
                    "id": "chat-local",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "local-test",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 3,
                        "total_tokens": 10,
                    },
                },
            ]
        return rows
    if protocol == "anthropic":
        rows = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-local",
                    "type": "message",
                    "role": "assistant",
                    "model": "local-test",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 7, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Partial reply"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-local",
                    "name": "write_file",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"path":"a.md"}',
                },
            },
            {"type": "content_block_stop", "index": 1},
        ]
        if terminal:
            rows += [
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 3},
                },
                {"type": "message_stop"},
            ]
        return rows
    rows = [
        {
            "model": "local-test",
            "message": {"role": "assistant", "content": "Partial reply"},
            "done": False,
        },
        {
            "model": "local-test",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-local",
                        "function": {"name": "write_file", "arguments": '{"path":'},
                    }
                ],
            },
            "done": False,
        },
    ]
    if terminal:
        rows += [
            {
                "model": "local-test",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-local", "function": {"arguments": '"a.md"}'}}
                    ],
                },
                "done": False,
            },
            {
                "model": "local-test",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 7,
                "eval_count": 3,
            },
        ]
    return rows


@asynccontextmanager
async def server(protocol: str, rows: list[dict]):
    app = web.Application()

    async def reply(request):
        body = await request.json()
        assert body["model"] == "local-test"
        assert body["stream"] is True
        content_type = (
            "application/x-ndjson" if protocol == "ollama" else "text/event-stream"
        )
        response = web.StreamResponse(headers={"Content-Type": content_type})
        await response.prepare(request)
        for row in rows:
            if protocol == "ollama":
                wire = json.dumps(row) + "\n"
            elif protocol == "anthropic":
                wire = f"event: {row['type']}\ndata: {json.dumps(row)}\n\n"
            else:
                wire = f"data: {json.dumps(row)}\n\n"
            await response.write(wire.encode())
        if protocol == "openai":
            await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def processes(request):
        return web.json_response(
            {"models": [{"name": "local-test", "context_length": 8192}]}
        )

    app.router.add_post("/v1/chat/completions", reply)
    app.router.add_post("/v1/messages", reply)
    app.router.add_post("/api/chat", reply)
    app.router.add_get("/api/ps", processes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


def provider_for(protocol: str, endpoint: str):
    if protocol == "ollama":
        path = (
            Path(__file__).resolve().parents[2]
            / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
        )
        spec = importlib.util.spec_from_file_location("local_ollama_cutoff", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.OllamaProvider({"model": "local-test", "endpoint": endpoint})
    cls = OpenAIProvider if protocol == "openai" else AnthropicProvider
    return cls(
        model="local-test",
        credential=Credential("local-http-test", "api_key", "local-only"),
        base_url=endpoint + "/v1" if protocol == "openai" else endpoint,
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "ollama"])
@pytest.mark.parametrize("method", ["stream", "complete"])
@pytest.mark.parametrize("terminal", [False, True])
async def test_real_http_terminal_required_before_tool_and_completion(
    protocol, method, terminal
):
    async with server(protocol, frames_for(protocol, terminal)) as endpoint:
        provider = provider_for(protocol, endpoint)
        events = []
        source = (
            provider.stream("request")
            if method == "stream"
            else provider.complete([{"role": "user", "content": "request"}])
        )
        try:
            if terminal:
                async for event in source:
                    events.append(event)
            else:
                with pytest.raises(AnswerCutOff) as caught:
                    async for event in source:
                        events.append(event)
                assert caught.value.model == "local-test"
                assert caught.value.chat_meta()["cut_off"]["adapter"]
            assert (
                "".join(e.text for e in events if e.kind == EVENT_TEXT_CHUNK)
                == "Partial reply"
            )
            calls = [e for e in events if e.kind == EVENT_TOOL_CALL]
            completed = [e for e in events if e.kind == EVENT_COMPLETE]
            if terminal:
                assert len(calls) == len(completed) == 1
                assert json.loads(calls[0].tool_input) == {"path": "a.md"}
                assert calls[0].stop_reason in {"stop", "tool_calls", "tool_use"}
                assert completed[0].input_tokens == 7
                assert completed[0].output_tokens == 3
            else:
                assert not calls and not completed
                if protocol != "ollama":
                    assert not any(
                        row.get("role") == "assistant" for row in provider._history
                    )
        finally:
            await source.aclose()
            await provider.shutdown()


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "ollama"])
@pytest.mark.parametrize("guarded", [False, True])
async def test_text_collector_propagates_cutoff(
    protocol, guarded, tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    async with server(protocol, frames_for(protocol, False)) as endpoint:
        provider = provider_for(protocol, endpoint)
        breaker = CircuitBreaker("local-" + protocol, threshold=1)
        guard = ModelCallGuard(
            provider,
            use_case="background",
            provider_name="local-" + protocol,
            model="local-test",
            breaker=breaker,
            meter=SpendMeter(config_dir=tmp_path),
        )
        try:
            with pytest.raises(AnswerCutOff) as caught:
                await stream_and_collect(guard if guarded else provider, "request")
            if guarded:
                from gideon.security.guardrails.audit import _audit_path

                assert breaker.state().value == "open"
                audit = json.loads(_audit_path().read_text().splitlines()[-1])
                assert audit["passed"] is False
                assert audit["failure_mode"] == "provider_error"
            assert "cut off" in humanize_provider_error(caught.value)
            assert "incomplete" in humanize_provider_error(caught.value)
        finally:
            await provider.shutdown()


def test_wrapped_failure_keeps_cutoff_identity():
    cause = AnswerCutOff(
        adapter="OpenAI-compatible", missing="a finish_reason", model="local-test"
    )
    wrapper = RuntimeError("call failed")
    wrapper.__cause__ = cause
    assert answer_cut_off(wrapper) is cause
    assert humanize_provider_error(wrapper) == cause.sentence()
