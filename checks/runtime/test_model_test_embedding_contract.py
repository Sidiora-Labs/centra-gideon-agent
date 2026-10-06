"""Owner embedding Test uses the actual selected Ollama adapter over local HTTP."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import ClientSession, web

from gideon.extensions.providers import model_test
from gideon.integrations.llm.registry import (
    get_default_registry,
    sync_entries_from_config,
)
from gideon.interfaces.dashboard.handlers.model_registry import api_model_test


@pytest.mark.asyncio
async def test_selected_embedding_http_owner_refusal_timeout_and_binding_preservation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    registry = get_default_registry()
    original = dict(registry._entries)
    registry._entries.clear()
    source = (
        Path(__file__).parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    spec = importlib.util.spec_from_file_location("embedding_test_ollama", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests = []
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def transport(request):
        body = await request.json()
        requests.append(body)
        assert request.path == "/api/embed"
        if body["model"] == "refused":
            return web.json_response({"error": "unavailable"}, status=503)
        if body["model"] == "slow":
            entered.set()
            await finish.wait()
        vector = (
            []
            if body["model"] == "empty"
            else [float("nan")] if body["model"] == "invalid" else [0.1, -0.2, 0.3]
        )
        return web.json_response({"embeddings": [vector]})

    provider_app = web.Application()
    provider_app.router.add_post("/api/embed", transport)
    provider_runner = web.AppRunner(provider_app)
    await provider_runner.setup()
    provider_site = web.TCPSite(provider_runner, "127.0.0.1", 0)
    await provider_site.start()
    endpoint = f"http://127.0.0.1:{provider_site._server.sockets[0].getsockname()[1]}"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "Local",
                        "type": "ollama",
                        "model": "wrong-default",
                        "options": {"endpoint": endpoint},
                    }
                ]
            }
        )
    )
    binding = tmp_path / "active_models.json"
    binding.write_text(
        json.dumps({"chat": ["Local:wrong-chat"], "embedding": "Local:wrong-embedding"})
    )
    before = (config.read_bytes(), binding.read_bytes())
    sync_entries_from_config()

    @web.middleware
    async def identity(request, handler):
        if request.headers.get("Test-Identity") == "owner":
            request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/test", api_model_test)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/test"
    monkeypatch.setattr(model_test, "_timeout_secs", lambda: 2)
    try:
        async with ClientSession() as client:

            async def probe(model, owner=True):
                async with client.post(
                    url,
                    headers={"Test-Identity": "owner"} if owner else {},
                    json={"use_case": "embedding", "model": model},
                ) as response:
                    return response.status, (
                        await response.json() if response.status != 403 else {}
                    )

            assert (await probe("Local:chosen", False))[0] == 403 and not requests
            status, result = await probe("Local:chosen")
            assert status == 200 and result["ok"] and "3 dimensions" in result["detail"]
            assert requests == [{"model": "chosen", "input": ["hello"]}]
            assert (await probe("Missing:chosen"))[0] == 409 and len(requests) == 1
            for name in ("refused", "empty", "invalid"):
                status, result = await probe(f"Local:{name}")
                assert status == 200 and not result["ok"]
            slow = asyncio.create_task(probe("Local:slow"))
            await entered.wait()
            assert (await probe("Local:chosen"))[0] == 409
            finish.set()
            assert (await slow)[1]["ok"]
            entered.clear()
            finish.clear()
            monkeypatch.setattr(model_test, "_timeout_secs", lambda: 0.05)
            status, result = await probe("Local:slow")
            assert status == 200 and result["reason"] == "timeout"
            await asyncio.sleep(0.05)
            from gideon.security.guardrails.local_inference import _RESOURCES

            assert not _RESOURCES
            assert (config.read_bytes(), binding.read_bytes()) == before
    finally:
        finish.set()
        await runner.cleanup()
        await provider_runner.cleanup()
        registry._entries.clear()
        registry._entries.update(original)


@pytest.mark.asyncio
async def test_direct_declared_refusal_and_selected_adapter(monkeypatch):
    from gideon.integrations.embedding_providers import registry
    from gideon.integrations.embedding_providers.base import EmbeddingProvider

    class Direct(EmbeddingProvider):
        name = "typed"
        display_name = "Typed"
        reason = ""
        calls = []

        async def is_available(self):
            return True

        def untestable_reason(self):
            return self.reason

        async def embed(self, text, model=""):
            self.calls.append((text, model))
            return [1.0, 2.0]

        async def embed_batch(self, texts, model=""):
            return [await self.embed(text, model) for text in texts]

    adapter = Direct()
    monkeypatch.setattr(
        registry, "direct_provider", lambda name: adapter if name == "typed" else None
    )
    adapter.reason = "This adapter needs setup."
    with pytest.raises(model_test.ModelUntestable):
        await model_test.run_model_test("embedding", "typed", "selected")
    assert not adapter.calls
    adapter.reason = ""
    result = await model_test.run_model_test("embedding", "typed", "selected")
    assert result.ok and adapter.calls == [("hello", "selected")]
