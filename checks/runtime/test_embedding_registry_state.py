"""Embedding selection and coroutine ownership with real storage and event loops."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.extensions.providers.use_cases import save_active_models
from gideon.integrations.embedding_providers import registry
from gideon.integrations.embedding_providers.base import (
    EmbeddingModel,
    run_embed_sync,
    sync_bridge_loop,
)


def test_active_selection_follows_the_current_home_and_keeps_model_colons(
    tmp_path, monkeypatch
):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(first))
    save_active_models({"embedding": ["native:first:v2", "native:fallback"]})
    monkeypatch.setenv("GIDEON_HOME", str(second))
    save_active_models({"embedding": ["sentence-transformers:second:v3"]})
    assert registry._active_embedding_spec() == ("sentence-transformers", "second:v3")
    monkeypatch.setenv("GIDEON_HOME", str(first))
    assert registry._active_embedding_spec() == ("native", "first:v2")
    assert json.loads((second / "active_models.json").read_text())["embedding"] == [
        "sentence-transformers:second:v3"
    ]


def test_no_binding_or_unqualified_first_selection_stays_disabled(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    assert registry._active_embedding_spec() is None
    assert registry.get_active_embed_fn() is None
    assert registry.get_active_embed_many_fn() is None
    assert registry.get_active_embedding_dim() is None
    save_active_models({"embedding": ["unqualified", "native:later"]})
    assert registry._active_embedding_spec() is None


@pytest.mark.asyncio
async def test_absent_native_extension_has_no_catalog_or_inference(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(registry, "_providers", {})
    save_active_models({"embedding": ["sentence_transformers:model"]})
    assert registry.native_provider() is None
    assert await registry.list_native_models() == []
    assert await registry.is_native_model_downloaded("model") is False
    assert await registry.delete_native_model("model") is False
    assert registry.get_active_embed_fn() is None
    assert registry.get_active_embed_many_fn() is None
    assert registry.get_active_embedding_dim() is None


def test_model_catalog_record_retains_management_and_dimension_fields():
    model = EmbeddingModel(
        name="local",
        dimension=384,
        size_mb=90.5,
        description="Text embedding",
        downloaded=True,
        active=True,
    )
    assert vars(model) == {
        "name": "local",
        "dimension": 384,
        "size_mb": 90.5,
        "description": "Text embedding",
        "downloaded": True,
        "active": True,
    }


def test_concurrent_async_callers_share_one_running_worker_loop():
    count = 6
    barrier = threading.Barrier(count)

    async def location():
        await asyncio.sleep(0)
        return asyncio.get_running_loop(), threading.get_ident()

    def caller():
        async def visit():
            barrier.wait(timeout=5)
            caller_id = threading.get_ident()
            loop, worker_id = run_embed_sync(location, timeout=2)
            return loop, worker_id, caller_id

        return asyncio.run(visit())

    with ThreadPoolExecutor(max_workers=count) as callers:
        results = list(callers.map(lambda _: caller(), range(count)))
    assert {id(loop) for loop, _, _ in results} == {id(sync_bridge_loop())}
    assert len({worker_id for _, worker_id, _ in results}) == 1
    assert all(worker_id != caller_id for _, worker_id, caller_id in results)


def test_timed_out_operation_is_cancelled_on_the_worker():
    cancelled = threading.Event()

    async def blocked():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def caller():
        with pytest.raises(TimeoutError):
            run_embed_sync(blocked, timeout=0.03)

    asyncio.run(caller())
    assert cancelled.wait(timeout=2)


def test_sync_caller_keeps_its_thread_and_closes_its_owned_loop():
    caller = threading.get_ident()

    async def location():
        return asyncio.get_running_loop(), threading.get_ident()

    loop, worker = run_embed_sync(location, timeout=1)
    assert worker == caller
    assert loop.is_closed()
