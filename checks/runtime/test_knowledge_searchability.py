import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.cognition.knowledge.embedder import UnifiedEmbedder
from gideon.cognition.knowledge.pipeline import TERMINAL_STAGES
from gideon.cognition.knowledge.pipeline.graphs import TERMINAL_STAGES as GRAPH_STAGES
from gideon.cognition.knowledge.pipeline.runner import embed_item_chunks, ingest_item
from gideon.cognition.knowledge.retrieval import HybridRetriever
from gideon.cognition.knowledge.store import KnowledgeStore


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def store(tmp_path):
    value = KnowledgeStore(str(tmp_path / "knowledge.db"))
    yield value
    value.close()


@pytest.fixture()
def embedder():
    return UnifiedEmbedder(lambda text: [1.0, 0.0, 0.0, 0.0], dim_hint=4)


def _phases(store, item_id):
    return (store.get_item(item_id).get("file_metadata") or {}).get("node_phases") or {}


def test_dedup_is_a_shared_terminal_stage():
    assert TERMINAL_STAGES is GRAPH_STAGES
    assert TERMINAL_STAGES == ("insights", "entities", "intents", "embed", "dedup")


def test_dedup_phase_reports_skipped_and_done_from_real_ingests(store, embedder):
    skipped = store.create_typed_item(
        item_type="note", title="Without vectors", content="semantic dedup cannot run"
    )
    skipped_events = []
    _run(
        ingest_item(
            store,
            skipped,
            embedder=None,
            publish=lambda event, data: skipped_events.append((event, data)),
        )
    )

    assert _phases(store, skipped)["dedup"] == "skipped"
    assert any(
        event == "node"
        and data.get("node") == "dedup"
        and data.get("phase") == "skipped"
        for event, data in skipped_events
    )

    done = store.create_typed_item(
        item_type="note", title="With vectors", content="semantic comparison can run"
    )
    done_events = []
    _run(
        ingest_item(
            store,
            done,
            embedder=embedder,
            publish=lambda event, data: done_events.append((event, data)),
        )
    )

    assert _phases(store, done)["dedup"] == "done"
    assert any(
        event == "node" and data.get("node") == "dedup" and data.get("phase") == "done"
        for event, data in done_events
    )


def test_dedup_failure_is_nonfatal_but_persisted_and_emitted(store, embedder):
    first = store.create_typed_item(
        item_type="note",
        title="Architecture Overview",
        content="the complete architecture overview",
    )
    assert _run(ingest_item(store, first, embedder=embedder)) in ("done", "partial")

    store.db.execute(
        "CREATE TRIGGER block_semantic_archive "
        "BEFORE UPDATE OF is_archived ON items "
        "BEGIN SELECT RAISE(ABORT, 'archive blocked'); END"
    )
    store.db.commit()
    duplicate = store.create_typed_item(
        item_type="note",
        title="Architecture Overview.pdf",
        content="the architecture overview",
    )
    events = []

    status = _run(
        ingest_item(
            store,
            duplicate,
            embedder=embedder,
            publish=lambda event, data: events.append((event, data)),
        )
    )

    assert status in ("done", "partial")
    assert store.get_item(duplicate)["is_archived"] is False
    assert _phases(store, duplicate)["dedup"] == "failed"
    assert any(
        event == "node"
        and data.get("node") == "dedup"
        and data.get("phase") == "failed"
        for event, data in events
    )


class _SpaceEmbedder:
    embedding_provider = "provider-a"

    def __init__(self, model):
        self.embedding_model = model
        self._dim = 4

    def is_available(self):
        return True

    def embed(self, text):
        return [1.0, 0.0, 0.0, 0.0]

    def embed_for_item(self, title, summary, content=None):
        return self.embed(title)


def test_chunk_search_excludes_same_dimension_vectors_from_another_model(store):
    item_id = store.create_typed_item(
        item_type="note", title="Old space", content="# Deep\n\nsemantic needle"
    )
    old = _SpaceEmbedder("model-old")
    embed_item_chunks(store, item_id, store.get_item(item_id)["content"], old)

    same_space = HybridRetriever(store, embedder=old.embed)
    assert same_space._vector_search("needle") == [(item_id, 1)]

    current = HybridRetriever(store, embedder=_SpaceEmbedder("model-new").embed)
    assert current._vector_search("needle") == []
    assert current.vector_index_status["stale"] > 0
    assert current.stale_index_reasons == [
        {
            "code": "model_changed",
            "count": current.vector_index_status["stale"],
            "detail": "Chunk vectors were produced by another model.",
            "remedy": {
                "method": "POST",
                "path": "/api/models/embedding/reindex",
                "label": "Re-index embeddings",
            },
        }
    ]


def test_doctor_surfaces_chunk_space_reason_and_reindex_remedy(store):
    from gideon.interfaces.dashboard.handlers.doctor import (
        _embedding_index_doctor_row,
    )

    item_id = store.create_typed_item(
        item_type="note", title="Doctor stale space", content="body"
    )
    embed_item_chunks(
        store,
        item_id,
        store.get_item(item_id)["content"],
        _SpaceEmbedder("model-old"),
    )
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    app["knowledge_embedder"] = _SpaceEmbedder("model-new")
    request = make_mocked_request("GET", "/api/doctor/knowledge", app=app)

    row = _embedding_index_doctor_row(request)
    assert row is not None
    assert row["id"] == "knowledge.embedding-space"
    assert row["evidence"]["degraded"] is True
    assert row["evidence"]["reasons"][0]["code"] == "model_changed"
    assert row["evidence"]["remedy"] == {
        "method": "POST",
        "path": "/api/models/embedding/reindex",
        "label": "Re-index embeddings",
    }
