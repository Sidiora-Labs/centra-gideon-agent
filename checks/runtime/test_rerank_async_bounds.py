"""Real coroutine, executor and SQLite coverage for bounded retrieval."""

import asyncio
import threading
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge import retrieval
from gideon.cognition.knowledge.embedder import UnifiedEmbedder
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.interfaces.dashboard.handlers import knowledge
from gideon.interfaces.dashboard.state import ConsoleState


def test_completion_timeout_cancels_real_coroutine(monkeypatch):
    monkeypatch.setattr(retrieval, "_RERANK_TIMEOUT_SECS", 0.02)
    with pytest.raises(TimeoutError):
        retrieval._run_rerank_completion(asyncio.sleep(10))
    assert retrieval._run_rerank_completion(asyncio.sleep(0, result="[]")) == "[]"


@pytest.mark.asyncio
async def test_owned_pool_does_not_wait_for_cancellation_cleanup(monkeypatch):
    monkeypatch.setattr(retrieval, "_RERANK_TIMEOUT_SECS", 0.05)
    cleanup_started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    async def workload():
        try:
            await asyncio.sleep(10)
        finally:
            cleanup_started.set()
            release.wait(2)
            finished.set()

    start = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            retrieval._run_rerank_completion(workload())
        assert time.monotonic() - start < 1
        assert await asyncio.to_thread(cleanup_started.wait, 1)
        assert not finished.is_set()
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 1)
    deadline = time.monotonic() + 1
    while any(t.name.startswith("knowledge-rerank") for t in threading.enumerate()):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler", [knowledge.list_items, knowledge.search_for_context]
)
async def test_handlers_search_real_store_off_event_loop(
    tmp_path, monkeypatch, handler
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text("{}")
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="note", title="Needle", content="Needle content"
    )
    state = ConsoleState(sessions=None, start_time=time.time())
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    app["knowledge_embedder"] = UnifiedEmbedder(None)
    app.router.add_get("/search", handler)
    entered = threading.Event()
    release = threading.Event()
    search_threads = []
    main_thread = threading.get_ident()

    def trace(sql):
        if "items_fts MATCH" in sql:
            search_threads.append(threading.get_ident())
            entered.set()
            release.wait(1)

    store.db.set_trace_callback(trace)

    async def heartbeat():
        deadline = time.monotonic() + 2
        while not entered.is_set():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.001)
        release.set()

    try:
        async with TestClient(TestServer(app)) as client:
            pulse = asyncio.create_task(heartbeat())
            response = await client.get("/search?q=Needle")
            await pulse
            assert response.status == 200
            assert item_id in await response.text()
            assert search_threads and all(t != main_thread for t in search_threads)
    finally:
        release.set()
        store.db.set_trace_callback(None)
        store.close()
