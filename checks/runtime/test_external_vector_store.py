"""Focused contract coverage for the external knowledge vector-store seam."""

import json
import math

import pytest

from gideon.cognition.knowledge.chunking import Chunk
from gideon.cognition.knowledge.embedder import floats_to_bytes
from gideon.cognition.knowledge.retrieval import HybridRetriever
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.extensions.apps.manifest import PROVIDER_TYPES
from gideon.extensions.providers.registry import VectorStoreTypeHandler
from gideon.integrations.vector_store_providers.registry import (
    register_provider,
    unregister_provider,
)
from gideon.sdk.vector_store import VectorStoreProvider


class MemoryVectorStore(VectorStoreProvider):
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.rows: dict[str, list[dict]] = {}

    @property
    def name(self) -> str:
        return "contract-memory"

    def search(self, embedding: list[float], *, limit: int) -> list[dict]:
        if self.fail:
            raise ConnectionError("unreachable")
        hits = []
        qnorm = math.sqrt(sum(value * value for value in embedding))
        for rows in self.rows.values():
            for row in rows:
                vector = row["embedding"]
                norm = math.sqrt(sum(value * value for value in vector))
                similarity = (
                    sum(a * b for a, b in zip(embedding, vector)) / (qnorm * norm)
                    if qnorm and norm
                    else 0.0
                )
                hits.append({**row, "similarity": similarity})
        return sorted(hits, key=lambda hit: hit["similarity"], reverse=True)[:limit]

    def replace_item(self, item_id: str, chunks: list[dict]) -> None:
        if self.fail:
            raise ConnectionError("unreachable")
        self.rows[item_id] = chunks

    def delete_item(self, item_id: str) -> None:
        if self.fail:
            raise ConnectionError("unreachable")
        self.rows.pop(item_id, None)

    def describe(self) -> dict:
        if self.fail:
            raise ConnectionError("unreachable")
        return {
            "name": self.name,
            "reachable": True,
            "count": sum(len(rows) for rows in self.rows.values()),
        }


@pytest.fixture(autouse=True)
def _unbind_vector_store():
    unregister_provider("contract-memory")
    yield
    unregister_provider("contract-memory")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from gideon.cognition.knowledge.store import knowledge_db_path

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    from gideon.cognition import knowledge

    monkeypatch.setattr(knowledge, "_store", store)
    yield store
    store.close()


def _item_with_chunk(store, title: str, vector: list[float], *, archived=False) -> str:
    item_id = store.create_typed_item(
        item_type="note", title=title, content=f"{title} reference material"
    )
    store.replace_chunks(
        item_id,
        [
            Chunk(
                text=title,
                section=title,
                line_start=1,
                line_end=1,
                chunk_index=0,
                embedding=floats_to_bytes(vector),
            )
        ],
    )
    if archived:
        store.update_item(item_id, is_archived=1)
    return item_id


def test_external_store_is_byte_identical_over_seven_queries_and_archive_modes(store):
    vectors = {
        "alpha": [1.0, 0.0, 0.0, 0.0],
        "beta": [0.8, 0.6, 0.0, 0.0],
        "gamma": [0.0, 1.0, 0.0, 0.0],
    }
    for title, vector in vectors.items():
        _item_with_chunk(store, title, vector, archived=title == "gamma")
    queries = [
        [1.0, 0.0, 0.0, 0.0],
        [0.8, 0.6, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.6, 0.8, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0],
        [0.7, 0.7, 0.0, 0.0],
        [0.1, 0.9, 0.0, 0.0],
    ]
    baseline = []
    for include_archived in (False, True):
        for query in queries:
            baseline.append(
                HybridRetriever(store, embedder=lambda _text, q=query: q).search(
                    "absent", include_archived=include_archived
                )
            )

    provider = MemoryVectorStore()
    register_provider(provider, store=store)
    for row in store.db.execute("SELECT id FROM items"):
        item_id = row["id"]
        chunks = store.get_chunks(item_id, with_embedding=True)
        provider.replace_item(
            item_id,
            [{"chunk_id": chunk["id"], **chunk} for chunk in chunks],
        )
    external = []
    for include_archived in (False, True):
        for query in queries:
            external.append(
                HybridRetriever(store, embedder=lambda _text, q=query: q).search(
                    "absent", include_archived=include_archived
                )
            )
    assert json.dumps(external, sort_keys=True) == json.dumps(baseline, sort_keys=True)


def test_unreachable_store_has_no_vec_fallback_and_writes_fail_open(store, caplog):
    item_id = _item_with_chunk(store, "keyword", [1.0, 0.0, 0.0, 0.0])
    provider = MemoryVectorStore(fail=True)
    register_provider(provider, store=store)
    store.replace_chunks(item_id, [])
    store.clear_chunks(item_id)
    results = HybridRetriever(
        store, embedder=lambda _text: [1.0, 0.0, 0.0, 0.0]
    ).search("keyword")
    assert results and results[0]["match_type"] == "keyword"
    store.delete_item(item_id)
    assert "External vector-store search failed" in caplog.text
    assert "External vector-store write failed" in caplog.text
    assert "External vector-store delete failed" in caplog.text


def test_provider_type_and_handler_are_public_contracts():
    assert "vector_store" in PROVIDER_TYPES
    assert VectorStoreTypeHandler


def test_registration_reindexes_existing_vectors_and_matching_counts_skip(store):
    item_id = _item_with_chunk(store, "before registration", [0.1, 0.2, 0.3])
    provider = MemoryVectorStore()
    register_provider(provider)
    rows = provider.rows[item_id]
    assert len(rows) == 1
    assert rows[0]["embedding"] == pytest.approx([0.1, 0.2, 0.3])
    report = store.reindex_external_vector_store()
    assert report == {
        "ok": True,
        "skipped": True,
        "reindexed": 0,
        "local_count": 1,
        "external_count": 1,
    }
    assert provider.rows[item_id] is rows
    provider.rows.clear()
    report = store.reindex_external_vector_store()
    assert report["ok"] and not report["skipped"]
    assert report["reindexed"] == 1
    assert provider.rows[item_id][0]["chunk_id"] == rows[0]["chunk_id"]


@pytest.mark.parametrize("width", [0, 4, 11, 13])
def test_external_row_width_is_not_swallowed(store, width, caplog):
    from gideon.cognition.knowledge.store import _EXTERNAL_ROW_COLUMNS

    assert len(_EXTERNAL_ROW_COLUMNS) == 12
    provider = MemoryVectorStore()
    register_provider(provider, store=store)
    with pytest.raises(ValueError, match="12 columns"):
        store._sync_external_vectors("invalid", [tuple(range(width))])
    assert provider.rows == {}
    assert "External vector-store write failed" not in caplog.text


def test_stale_chunk_refresh_preserves_ids_and_mirrors_full_item(store):
    from test_knowledge_chunking import _CharEmbedder

    provider = MemoryVectorStore()
    register_provider(provider, store=store)
    item_id = _item_with_chunk(store, "stale chunk", [1.0, 0.0, 0.0])
    original = store.get_chunks(item_id, with_embedding=True)[0]
    store.db.execute(
        "UPDATE chunks SET embedding_provider = 'retired', embedding_model = 'old' WHERE item_id = ?",
        (item_id,),
    )
    store.db.commit()
    embedder = _CharEmbedder()
    report = store.reembed_stale_chunks(embedder, limit=1)
    assert report == {"reembedded": 1, "failed": 0, "total": 1}
    updated = store.get_chunks(item_id, with_embedding=True)[0]
    assert updated["id"] == original["id"]
    assert updated["embedding"] == embedder.embed(original["text"])
    assert provider.rows[item_id][0]["chunk_id"] == original["id"]
    assert provider.rows[item_id][0]["embedding"] == updated["embedding"]
    assert store.reembed_stale_chunks(embedder)["total"] == 0


def test_reindex_failure_cannot_report_success(store):
    _item_with_chunk(store, "content", [1.0, 0.0])
    provider = MemoryVectorStore()
    register_provider(provider, store=store)
    provider.rows["unowned"] = [{"embedding": [1.0, 0.0]}]
    report = store.reindex_external_vector_store()
    assert not report["ok"]
    assert report["external_count"] == 2
    assert report["local_count"] == 1
    provider.fail = True
    with pytest.raises(ConnectionError):
        store.reindex_external_vector_store()


@pytest.mark.parametrize("state", ["healthy", "unreachable", "empty", "both_empty"])
def test_doctor_reports_bound_store_and_fails_unhealthy_states(
    store, tmp_path, capsys, state
):
    import asyncio

    from gideon.interfaces.cli.doctor import _doctor_external_vector_store
    from gideon.operations.resilience.doctor import (
        DoctorContext,
        _probe_external_vector_store,
    )

    if state != "both_empty":
        _item_with_chunk(store, "local", [1.0, 0.0])
    provider = MemoryVectorStore()
    register_provider(provider, store=store)
    if state == "unreachable":
        provider.fail = True
    if state == "empty":
        provider.rows.clear()
    result = asyncio.run(_probe_external_vector_store(DoctorContext(home=tmp_path)))
    assert result.ok == (state in {"healthy", "both_empty"})
    issues = _doctor_external_vector_store()
    output = capsys.readouterr().out
    assert bool(issues) == (not result.ok)
    if state == "unreachable":
        assert "unreachable" in output
    else:
        assert json.dumps(provider.describe(), sort_keys=True) in output
    if state == "empty":
        assert "empty while 1 local chunk vectors exist" in output
    assert (
        provider.rows == {} if state in {"empty", "both_empty"} else bool(provider.rows)
    )
