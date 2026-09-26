import json

import pytest
from aiohttp import web

from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.anthropic import AnthropicProvider
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider, _ChatDecoder


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
@pytest.mark.parametrize("override,capacity", [("8192", 8192), (None, 4096)])
async def test_binding_strips_override_and_measures_served_capacity(
    kind, override, capacity
):
    requests = []

    async def inference(request):
        requests.append(await request.json())
        if kind == "openai":
            frames = [
                {
                    "id": "turn",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "llama3.1",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2048,
                        "prompt_tokens_details": {"cached_tokens": 0},
                        "completion_tokens": 1,
                        "total_tokens": 2049,
                    },
                }
            ]
            body = (
                "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
                + "data: [DONE]\n\n"
            )
        else:
            frames = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "turn",
                            "type": "message",
                            "role": "assistant",
                            "model": "llama3.1",
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 2048, "output_tokens": 0},
                        },
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "done"},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 1},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            body = "".join(
                f"event: {name}\ndata: {json.dumps(frame)}\n\n"
                for name, frame in frames
            )
        return web.Response(text=body, content_type="text/event-stream")

    app = web.Application()
    app.router.add_post("/v1/chat/completions", inference)
    app.router.add_post("/v1/messages", inference)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    if kind == "openai":
        endpoint += "/v1"
    provider_cls = OpenAIProvider if kind == "openai" else AnthropicProvider
    provider = provider_cls(
        model="llama3.1",
        credential=Credential(name="local", kind="api_key", secret="local"),
        base_url=endpoint,
        extra_options={"context_window": override},
    )
    try:
        events = [event async for event in provider.stream("hello")]
        assert requests and "context_window" not in requests[0]
        assert events[-1].context_usage_pct == pytest.approx(2048 / capacity * 100)
        usage = events[-1].context_usage
        assert usage is not None
        assert usage.input_tokens == 2048
        assert usage.total_input_tokens == (2048 if kind == "openai" else None)
        assert usage.cache_creation_tokens is None
        assert usage.cache_read_tokens == (0 if kind == "openai" else None)
        assert usage.context_window_tokens == (8192 if override else None)
        assert provider.context_usage_pct() == events[-1].context_usage_pct
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="context-test", model="llama3.1"),
            model_provider=provider,
        )
        runtime._messages = [{"role": "user", "content": "x" * 6144}]
        from gideon.cognition.context_compaction import total_chars

        assert runtime._estimated_context_pct() == pytest.approx(
            total_chars(runtime._messages) / 3.0 / capacity * 100
        )
    finally:
        await provider.shutdown()
        await runner.cleanup()


def test_openai_cached_tokens_still_use_declared_capacity():
    decoder = _ChatDecoder()
    decoder.feed(
        {
            "usage": {
                "prompt_tokens": 4096,
                "prompt_tokens_details": {"cached_tokens": 2048},
            }
        }
    )
    assert decoder.context_usage_pct("llama3.1", override="8192", local=True) == 50
    usage = decoder.usage.terminal(50, context_window_tokens=8192).context_usage
    assert usage is not None
    assert usage.as_payload() == {
        "input_tokens": 2048,
        "cache_creation_tokens": None,
        "cache_read_tokens": 2048,
        "context_window_tokens": 8192,
        "total_input_tokens": 4096,
    }


@pytest.mark.asyncio
async def test_openai_served_windows_stay_bound_to_each_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_hosts": ["127.0.0.1"]}}})
    )
    runners = []
    providers = []

    async def serve(model_rows):
        async def models(_request):
            return web.json_response({"object": "list", "data": model_rows})

        async def inference(_request):
            frame = {
                "id": "turn",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "same-model",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens": 1,
                },
            }
            return web.Response(
                text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n",
                content_type="text/event-stream",
            )

        app = web.Application()
        app.router.add_get("/v1/models", models)
        app.router.add_post("/v1/chat/completions", inference)
        runner = web.AppRunner(app)
        await runner.setup()
        runners.append(runner)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
        provider = OpenAIProvider(
            model="same-model",
            credential=Credential(name="local", kind="api_key", secret="local"),
            base_url=endpoint,
        )
        providers.append(provider)
        await provider.start()
        return provider

    try:
        first = await serve([
            {"id": "other-embedding", "context_length": 4000},
            {"id": "same-model", "context_window": 8192},
        ])
        second = await serve([{"id": "same-model", "context_length": 16384}])
        third = await serve([{"id": "same-model", "context_length": "unknown"}])
        first_events = [event async for event in first.stream("hello")]
        second_events = [event async for event in second.stream("hello")]
        third_events = [event async for event in third.stream("hello")]
        assert first_events[-1].context_usage.context_window_tokens == 8192
        assert first_events[-1].context_usage.total_input_tokens == 100
        assert second_events[-1].context_usage.context_window_tokens == 16384
        assert third_events[-1].context_usage.context_window_tokens is None
    finally:
        for provider in providers:
            await provider.shutdown()
        for runner in runners:
            await runner.cleanup()
