"""Qualification coverage for the optional knowledge relevance reranker."""

from __future__ import annotations

from gideon.assurance.evals.runner import _coords_from_run_output
from gideon.cognition.knowledge.retrieval import (
    HybridRetriever,
    RelevanceReranker,
    RerankerHealth,
    _rank_scored_candidates,
)
from gideon.cognition.knowledge.store import KnowledgeStore


def test_reranker_is_off_with_a_typed_non_available_result_by_default():
    reranker = RelevanceReranker()

    result = reranker.rerank(
        "rollback deployment",
        [{"id": "one", "title": "Rollback deployment", "content": "runbook"}],
    )

    assert result.applied is False
    assert result.candidates == ()
    assert result.health == RerankerHealth(
        state="disabled",
        enabled=False,
        available=False,
        model="cross-encoder/ms-marco-MiniLM-L-6-v2",
        reason="disabled",
    )


def test_model_scores_are_normalized_and_order_candidates_stably():
    candidates = [
        {
            "id": "partial",
            "title": "Deployment notes",
            "summary": "rollback appendix",
            "content": "general release notes",
        },
        {
            "id": "exact",
            "title": "Rollback deployment",
            "summary": "production runbook",
            "content": "steps and checks",
        },
    ]

    ranked = _rank_scored_candidates(candidates, [-2.0, 4.0])

    assert [candidate.item_id for candidate in ranked] == ["exact", "partial"]
    assert 0.0 < ranked[1].score < ranked[0].score < 1.0


def test_tied_model_scores_preserve_fusion_order():
    candidates = [
        {"id": "first", "title": "First", "content": "body"},
        {"id": "second", "title": "Second", "content": "body"},
    ]

    ranked = _rank_scored_candidates(candidates, [0.0, 0.0])

    assert [candidate.item_id for candidate in ranked] == ["first", "second"]


def test_no_candidates_does_not_load_an_enabled_model():
    reranker = RelevanceReranker(enabled=True, model_name="not-present-locally")

    result = reranker.rerank("anything", [])

    assert result.applied is False
    assert result.health.state == "unavailable"
    assert result.health.reason == "not_checked"
    assert reranker._load_attempted is False


def test_missing_local_model_falls_back_to_identical_hybrid_results(tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="note",
        title="Rollback deployment runbook",
        content="Rollback deployment steps and production checks.",
    )
    store.db.commit()
    missing = tmp_path / "no-such-reranker"
    reranker = RelevanceReranker(enabled=True, model_name=str(missing))

    try:
        baseline = HybridRetriever(store, reranker=RelevanceReranker()).search(
            "rollback deployment", limit=5
        )
        retriever = HybridRetriever(store, reranker=reranker)
        degraded = retriever.search("rollback deployment", limit=5)
    finally:
        store.close()

    health = retriever.last_reranker_result.health
    assert [hit["id"] for hit in baseline] == [item_id]
    assert degraded == baseline
    assert health.state == "unavailable"
    assert health.enabled is True
    assert health.available is False
    assert health.reason in {"dependency_unavailable", "model_unavailable"}
    assert health.detail


def test_evaluation_arm_execution_comes_from_returned_run_output():
    requested = {"arm_mask": "off", "model": "local"}

    completed = _coords_from_run_output(
        requested,
        {
            "ok": True,
            "overlay": {"arm": "on", "applied": []},
        },
    )
    missing_application = _coords_from_run_output(
        requested,
        {
            "ok": True,
            "overlay": {"arm": "off", "applied": []},
        },
    )

    assert completed == {"arm_mask": "on", "model": "local"}
    assert missing_application == {"model": "local"}
