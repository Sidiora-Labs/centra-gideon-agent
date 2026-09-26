import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import web


def bundle():
    path = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    spec = importlib.util.spec_from_file_location("gideon_context_ollama", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override,served,status,expected",
    [
        ("16384", 8192, 200, 16384),
        (None, 8192, 200, 8192),
        (None, "32768", 200, 32768),
        (None, 0, 200, 4096),
        (None, 8192, 503, 4096),
        (None, 2048, 200, 2048),
    ],
)
async def test_live_http_context_precedence_and_gauge(
    override, served, status, expected
):
    requests = []
    probes = []

    async def chat(request):
        requests.append(await request.json())
        return web.Response(
            text=json.dumps(
                {
                    "message": {"content": "answer"},
                    "done": True,
                    "prompt_eval_count": 1024,
                    "eval_count": 2,
                }
            )
            + "\n",
            content_type="application/x-ndjson",
        )

    async def running(request):
        probes.append(request.path)
        return web.json_response(
            {
                "models": [
                    {"name": "other", "context_length": 999999},
                    {"name": "llama3.1:latest", "context_length": served},
                ]
            },
            status=status,
        )

    app = web.Application()
    app.router.add_post("/api/chat", chat)
    app.router.add_get("/api/ps", running)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    provider = bundle().create_provider(
        {
            "endpoint": endpoint,
            "model": "llama3.1",
            "context_window": override,
            "options": {"context_window": "99999"},
            "timeout_secs": "10",
        }
    )
    try:
        assert provider.context_usage_pct() is None
        events = [
            event
            async for event in provider.complete([{"role": "user", "content": "hello"}])
        ]
        assert provider.context_window == expected
        assert events[-1].context_usage_pct == pytest.approx(1024 / expected * 100)
        usage = events[-1].context_usage
        assert usage is not None
        assert usage.input_tokens == 1024
        assert usage.total_input_tokens == 1024
        assert usage.cache_creation_tokens is None
        assert usage.cache_read_tokens is None
        assert usage.context_window_tokens == (
            expected if override or (status == 200 and served) else None
        )
        assert provider.context_usage_pct() == events[-1].context_usage_pct
        assert "context_window" not in requests[0].get("options", {})
        assert len(probes) == (0 if override else 1)
    finally:
        await provider.shutdown()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("served", [8192, None])
async def test_missing_prompt_count_does_not_become_measured_usage(served):
    async def chat(_request):
        return web.Response(
            text=json.dumps({"message": {"content": "answer"}, "done": True}) + "\n",
            content_type="application/x-ndjson",
        )

    async def running(_request):
        return web.json_response(
            {"models": [{"name": "llama3.1", "context_length": served}]}
        )

    app = web.Application()
    app.router.add_post("/api/chat", chat)
    app.router.add_get("/api/ps", running)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    provider = bundle().create_provider({"endpoint": endpoint, "model": "llama3.1"})
    try:
        events = [event async for event in provider.stream("hello")]
        assert events[-1].input_tokens == 0
        assert events[-1].context_usage_pct is None
        if served is None:
            assert events[-1].context_usage is None
            assert provider.context_window == 4096
        else:
            assert events[-1].context_usage.as_payload() == {
                "input_tokens": None,
                "cache_creation_tokens": None,
                "cache_read_tokens": None,
                "context_window_tokens": served,
            }
            assert provider.context_window == served
    finally:
        await provider.shutdown()
        await runner.cleanup()
