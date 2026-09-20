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


@pytest.fixture(autouse=True)
def _unbind_vector_store():
    unregister_provider("contract-memory")
    yield
    unregister_provider("contract-memory")


@pytest.fixture()
def store(tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
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
    register_provider(provider)
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
    register_provider(provider)
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
