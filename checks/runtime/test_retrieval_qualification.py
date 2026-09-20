"""Focused qualification for KBVS-2's reasoning-model reranker."""

from __future__ import annotations

import json

from gideon.assurance.evals import retrieval_bench as rb
from gideon.cognition.knowledge.retrieval import HybridRetriever, RelevanceReranker
from gideon.cognition.knowledge.store import KnowledgeStore


def _store(tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    ids = [
        store.create_typed_item(item_type="note", title=title, content="shared token")
        for title in ("First", "Second")
    ]
    store.db.commit()
    return store, ids


def test_reranker_defaults_off_and_failures_keep_fusion_order(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    try:
        baseline = HybridRetriever(store, reranker=RelevanceReranker()).search("shared")

        async def broken(*args, **kwargs):
            raise RuntimeError("offline")

        monkeypatch.setattr(
            "gideon.integrations.llm_helpers.one_shot_completion", broken
        )
        degraded = HybridRetriever(
            store, reranker=RelevanceReranker(enabled=True)
        ).search("shared")
        assert [hit["id"] for hit in degraded] == [hit["id"] for hit in baseline]
    finally:
        store.close()


def test_model_order_drives_rerank_bench_from_one_to_zero(tmp_path, monkeypatch):
    store, ids = _store(tmp_path)
    response = [ids[0], ids[1]]

    async def ordered(prompt, *, use_case):
        assert use_case == "reasoning"
        return json.dumps(response)

    monkeypatch.setattr("gideon.integrations.llm_helpers.one_shot_completion", ordered)
    retriever = HybridRetriever(store, reranker=RelevanceReranker(enabled=True))
    query = rb.QrelsQuery("shared", (ids[0],))

    try:
        first = retriever.search(query.query, limit=1)
        one = rb.build_rerank_row(
            [rb.score_query(query, [first[0]["id"]], mask="rerank", k=1)]
        )
        response[:] = [ids[1], ids[0]]
        second = retriever.search(query.query, limit=1)
        zero = rb.build_rerank_row(
            [rb.score_query(query, [second[0]["id"]], mask="rerank", k=1)]
        )
        assert one.p_at_k == 1.0
        assert zero.p_at_k == 0.0
        assert rb.build_rerank_row([]).p_at_k == "not measured"
    finally:
        store.close()
