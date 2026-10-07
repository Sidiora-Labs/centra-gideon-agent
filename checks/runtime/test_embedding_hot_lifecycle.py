"""Real installed embedding adapters, local HTTP, and hot binding ownership."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.cognition.knowledge.embedder import UnifiedEmbedder
from gideon.extensions.providers import media_scanners
from gideon.extensions.providers.use_cases import save_active_models
from gideon.hypermid.models import Trace
from gideon.hypermid.providers import (
    EmbeddingProviderFailure,
    GideonEmbeddingProviderAuthority,
    ProviderBinding,
)
from gideon.integrations.embedding_providers import registry
from gideon.integrations.llm import registry as model_registry


def load_ollama():
    path = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    spec = importlib.util.spec_from_file_location("gideon_embedding_hot_ollama", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def embedding_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(registry, "_scanned", {})
    monkeypatch.setattr(registry, "_refresh_requested", False)
    monkeypatch.setattr(media_scanners, "_scanners", {})
    monkeypatch.setattr(
        model_registry, "_default_registry", model_registry.ProviderRegistry()
    )
    return tmp_path


@pytest.mark.asyncio
async def test_hot_adapter_settings_class_removal_and_native_ownership(embedding_home):
    module = load_ollama()
    requests = []

    async def embed(request):
        body = await request.json()
        requests.append(body)
        return web.json_response(
            {
                "embeddings": [
                    [1.0, 0.0] if body["model"] == "a" else [0.0, 1.0, 0.0]
                    for _ in body["input"]
                ]
            }
        )

    app = web.Application()
    app.router.add_post("/api/embed", embed)
    async with TestServer(app) as server:

        def save(endpoint):
            (embedding_home / "config.json").write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "name": "ollama-models",
                                "type": "ollama",
                                "options": {"endpoint": endpoint},
                            }
                        ]
                    }
                )
            )

        save(str(server.make_url("/")).rstrip("/"))

        def scanner(entries):
            return [
                module.create_provider(entry["options"])
                for entry in entries
                if entry["type"] == "ollama"
            ]

        media_scanners.register_scanner("embedding", scanner)
        save_active_models({"embedding": ["ollama-models:a"]})
        callback = registry.get_active_embed_fn()
        original = registry.get_provider("ollama-models")
        assert registry.get_provider("ollama-models") is original
        assert await asyncio.to_thread(callback, "first") == [1.0, 0.0]
        embedder = UnifiedEmbedder(callback, dim_hint=2)
        save_active_models({"embedding": ["ollama-models:b"]})
        assert await asyncio.to_thread(embedder.dim) == 3
        assert await asyncio.to_thread(callback, "new model") == [0.0, 1.0, 0.0]
        registry.refresh_providers()
        replaced = registry.get_provider("ollama-models")
        assert replaced is not original
        module = (
            load_ollama()
        )  # Real installed module reimport creates its new adapter class.
        media_scanners.register_scanner("embedding", scanner)
        reloaded = registry.get_provider("ollama-models")
        assert type(reloaded) is module.OllamaProvider
        assert reloaded is not replaced
        (embedding_home / "config.json").write_text("{corrupt")
        registry.refresh_providers()
        assert registry.get_provider("ollama-models") is reloaded
        save(str(server.make_url("/")).rstrip("/"))
        native = module.create_provider({"endpoint": str(server.make_url("/"))})
        native.name = "ollama-models"
        registry.register_provider(native)
        media_scanners.unregister_scanner("embedding", scanner)
        assert registry.get_provider("ollama-models") is native
        registry.unregister_provider("ollama-models")
        assert registry.get_provider("ollama-models") is None
        (embedding_home / "config.json").write_text(json.dumps({"providers": []}))
        assert await asyncio.to_thread(callback, "disabled") is None
        assert requests[0]["input"] == ["first"]


@pytest.mark.asyncio
async def test_registered_local_model_fallback_keeps_type_and_batch_contract(
    embedding_home,
):
    load_ollama()
    requests = []

    async def embed(request):
        body = await request.json()
        requests.append(body)
        assert all(isinstance(text, str) for text in body["input"])
        return web.json_response({"embeddings": [[1.0, 2.0] for _ in body["input"]]})

    app = web.Application()
    app.router.add_post("/api/embed", embed)
    async with TestServer(app) as server:
        catalog = model_registry.get_default_registry()
        catalog.register_entry(
            model_registry.ProviderEntry(
                name="local-profile",
                type="ollama",
                model="chat-default",
                options={"endpoint": str(server.make_url("/")).rstrip("/")},
            )
        )
        (embedding_home / "config.json").write_text(
            json.dumps(
                {
                    "providers": [
                        {
                            "name": "local-profile",
                            "type": "ollama",
                            "model": "chat-default",
                            "options": {
                                "endpoint": str(server.make_url("/")).rstrip("/")
                            },
                        }
                    ]
                }
            )
        )
        cap = catalog.capability_of("ollama")
        save_active_models({"embedding": ["local-profile:chosen-embedding"]})
        callback = registry.get_active_embed_fn()
        assert await asyncio.to_thread(callback, "one") == [1.0, 2.0]
        batch = registry.get_active_embed_many_fn()
        assert await asyncio.to_thread(batch, ["one", "two"]) == [
            [1.0, 2.0],
            [1.0, 2.0],
        ]
        assert catalog.capability_of("ollama") is cap
        assert requests == [
            {"model": "chosen-embedding", "input": ["one"]},
            {"model": "chosen-embedding", "input": ["one", "two"]},
        ]


@pytest.mark.asyncio
async def test_native_authority_discards_inflight_old_adapter_and_preserves_cancellation(
    embedding_home,
):
    module = load_ollama()
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def embed(request):
        await request.json()
        entered.set()
        await finish.wait()
        return web.json_response({"embeddings": [[1.0, 0.0]]})

    app = web.Application()
    app.router.add_post("/api/embed", embed)
    async with TestServer(app) as server:

        def scanner(entries):
            return [
                module.create_provider(
                    {"endpoint": str(server.make_url("/")).rstrip("/")}
                )
            ]

        media_scanners.register_scanner("embedding", scanner)
        (embedding_home / "config.json").write_text(
            json.dumps(
                {
                    "providers": [
                        {"name": "ollama-models", "type": "ollama", "options": {}}
                    ]
                }
            )
        )
        save_active_models({"embedding": ["ollama-models:a"]})
        authority = GideonEmbeddingProviderAuthority()
        trace = Trace("local-trace", "local-request")
        expected = ProviderBinding("ollama-models", "a", 2)
        task = asyncio.create_task(authority.embed("old request", expected, trace))
        await asyncio.wait_for(entered.wait(), 3)
        registry.refresh_providers()
        finish.set()
        with pytest.raises(EmbeddingProviderFailure) as error:
            await task
        assert error.value.error.code == "EMBEDDING_PROVIDER_CHANGED"
        entered.clear()
        finish.clear()
        task = asyncio.create_task(authority.embed("cancel request", expected, trace))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        finish.set()
