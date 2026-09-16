"""Native knowledge provider + ingest queue (#30 Task A — provider seam)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.integrations.knowledge_providers.native import (
    NATIVE_TYPES,
    create_native_provider,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store():
    return KnowledgeStore(str(Path(tempfile.mkdtemp()) / "k.db"))


def test_create_typed_registers_and_enqueues(store):
    enq = []
    prov = create_native_provider(store, enqueue=enq.append)
    iid = prov.create_typed(item_type="note", title="N", content="body")
    item = store.get_item(iid)
    assert item["provider"] == "native"
    assert item["processing_status"] == "queued"
    assert enq == [iid]


def test_create_typed_rejects_unknown_type(store):
    prov = create_native_provider(store)
    with pytest.raises(ValueError, match="unknown knowledge type"):
        prov.create_typed(item_type="hologram", title="x")


def test_create_typed_file_carries_path(store):
    prov = create_native_provider(store)
    iid = prov.create_typed(
        item_type="pdf",
        title="doc",
        file_path="/tmp/x.pdf",
        mime_type="application/pdf",
    )
    item = store.get_item(iid)
    assert item["file_path"] == "/tmp/x.pdf"
    assert item["mime_type"] == "application/pdf"


def test_provider_search_and_get(store):
    prov = create_native_provider(store)
    iid = prov.create_typed(
        item_type="note", title="Findable", content="unique haystack term"
    )
    results = _run(prov.search("haystack"))
    assert any(r.id == iid for r in results)
    got = _run(prov.get_item(iid))
    assert got and got.title == "Findable"


def test_provider_delete(store):
    prov = create_native_provider(store)
    iid = prov.create_typed(item_type="note", title="x", content="y")
    assert _run(prov.delete_item(iid)) is True
    assert store.get_item(iid) is None


def test_provider_lists_single_library_source(store):
    """The native provider is one library (no sub-partitioning) — list_sources reports
    a single 'native' source with the live active-item count."""
    prov = create_native_provider(store)
    prov.create_typed(item_type="note", title="a", content="x")
    prov.create_typed(item_type="note", title="b", content="y")
    sources = _run(prov.list_sources())
    assert len(sources) == 1
    assert sources[0].id == "native" and sources[0].item_count == 2


def test_thirteen_native_types():
    assert len(NATIVE_TYPES) == 13
    assert {"note", "pdf", "video", "bookmark", "decision"} <= set(NATIVE_TYPES)


def test_queue_enqueue_dedups(store):
    from gideon.cognition.knowledge.ingest_queue import KnowledgeIngestQueue

    async def go():
        q = KnowledgeIngestQueue(store)
        q.enqueue("a")
        q.enqueue("a")
        q.enqueue("b")
        return q.qsize()

    assert _run(go()) == 2


def test_queue_recovers_pending_items_on_start(store):
    """Items left in queued/processing by a prior (crashed) process are re-enqueued
    on startup — the in-memory queue would otherwise strand them forever."""
    from gideon.cognition.knowledge.ingest_queue import KnowledgeIngestQueue

    stuck_q = store.create_typed_item(
        item_type="note", title="Q", content="x", extra={"processing_status": "queued"}
    )
    stuck_p = store.create_typed_item(
        item_type="note",
        title="P",
        content="y",
        extra={"processing_status": "processing"},
    )
    assert stuck_q and stuck_p
    done = store.create_typed_item(
        item_type="note", title="D", content="z", extra={"processing_status": "done"}
    )

    async def go():
        q = KnowledgeIngestQueue(store)
        n = q.recover_pending()
        return n, q.qsize()

    n, size = _run(go())
    assert n == 2 and size == 2
    assert done


def test_queue_processes_item_end_to_end(store):
    from gideon.cognition.knowledge.ingest_queue import KnowledgeIngestQueue
    from gideon.cognition.knowledge.pipeline import ensure_nodes_registered

    ensure_nodes_registered()

    async def go():
        iid = store.create_typed_item(
            item_type="note",
            title="N",
            content="queue body",
            extra={"processing_status": "queued"},
        )
        q = KnowledgeIngestQueue(store)
        q.start()
        q.enqueue(iid)
        for _ in range(50):
            await asyncio.sleep(0.05)
            if store.get_item(iid)["processing_status"] == "done":
                break
        q.stop()
        return store.get_item(iid)["processing_status"]

    assert _run(go()) == "done"
