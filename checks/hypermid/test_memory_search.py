from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from gideon.hypermid.models import Cursor, Scope, Trace
from gideon.hypermid.prompt_projection import (
    acknowledge_explicit_memory,
    project_automatic_memory,
)
from gideon.hypermid.search import (
    CandidateVector,
    Provenance,
    SearchCandidate,
    SearchEngine,
    SearchError,
    SearchMode,
    SearchRequest,
)


NOW = 1_800_000_000_000
FINGERPRINT = "f" * 64


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def candidate(
    identity: str,
    content: str,
    *,
    owner: str = "alice",
    source: str = "memory",
    status: str = "active",
    vector_digest: str | None = None,
) -> SearchCandidate:
    content_digest = digest(content)
    return SearchCandidate(
        id=identity,
        owner_scope=Scope(owner, "project"),
        source=source,
        kind="anchor" if identity == "anchor" else "fact",
        category="general",
        content=content,
        content_digest=content_digest,
        status=status,
        source_time_ms=NOW - 1000,
        importance=0.7,
        verification="supported",
        provenance=(
            Provenance("message", digest("source"), NOW, "message_snapshot"),
        ),
        vector=CandidateVector(
            (1.0, 0.0), FINGERPRINT, vector_digest or content_digest
        ),
    )


def request(**changes: object) -> SearchRequest:
    values = {
        "query": "launch checklist",
        "scope": Scope("alice", "project"),
        "mode": SearchMode.HYBRID,
        "limit": 10,
        "trace": Trace("trace", "request"),
        "now_ms": NOW,
        "query_vector": (1.0, 0.0),
        "vector_fingerprint": FINGERPRINT,
    }
    values.update(changes)
    return SearchRequest(**values)


def test_mixed_source_hybrid_search_is_stable_explainable_and_delivery_safe() -> None:
    engine = SearchEngine()
    candidates = [
        candidate("message", "Launch checklist approvals", source="message"),
        candidate("anchor", "Launch checklist is final", source="memory"),
        candidate("file", "Deployment launch checklist", source="file"),
    ]

    first = engine.search(request(), candidates, cursor=Cursor(2, 9))
    second = engine.search(request(), reversed(candidates), cursor=Cursor(2, 9))

    assert [hit.id for hit in first.hits] == [hit.id for hit in second.hits]
    assert {hit.source for hit in first.hits} == {"memory", "message", "file"}
    assert all(hit.scores.lexical > 0 for hit in first.hits)
    assert all(hit.scores.semantic == pytest.approx(1.0) for hit in first.hits)
    assert all(hit.provenance for hit in first.hits)
    automatic = project_automatic_memory(engine, first)
    assert "[memory:anchor]" in automatic.content
    assert engine.explicit_retrieval_count(first.hits[0].id) == 0
    acknowledge_explicit_memory(engine, first)
    assert all(engine.explicit_retrieval_count(hit.id) == 1 for hit in first.hits)


def test_visibility_state_freshness_and_context_are_filtered_before_content_leaks() -> None:
    engine = SearchEngine()
    visible = candidate("visible", "launch checklist visible")
    stale = candidate(
        "stale-vector",
        "launch checklist changed",
        vector_digest=digest("previous content"),
    )
    expired = candidate("expired", "launch checklist expired")
    expired = replace(expired, expires_at_ms=NOW)
    archived = candidate("archived", "launch checklist archived", status="archived")
    tombstoned = candidate("tombstoned", "launch checklist deleted", status="tombstoned")
    foreign = candidate("foreign", "private launch checklist", owner="bob")

    response = engine.search(
        request(visible_digests=frozenset({visible.content_digest})),
        [visible, stale, expired, archived, tombstoned, foreign],
        cursor=Cursor(1, 4),
    )

    assert response.hits == ()
    assert response.suppressed.unauthorized == 1
    assert response.suppressed.state == 3
    assert response.suppressed.stale == 1
    assert response.suppressed.visible == 1
    assert "private launch checklist" not in repr(response)


def test_semantic_required_failure_is_typed_while_hybrid_falls_back_to_lexical() -> None:
    engine = SearchEngine()
    item = candidate("record", "launch checklist")
    degraded = engine.search(
        request(query_vector=None, vector_fingerprint=None),
        [item],
        cursor=Cursor(1, 1),
    )
    assert degraded.degraded
    assert degraded.degradation_reason == "semantic_unavailable"
    assert degraded.hits[0].id == "record"

    with pytest.raises(SearchError) as unavailable:
        engine.search(
            request(
                mode=SearchMode.SEMANTIC,
                semantic_required=True,
                query_vector=None,
                vector_fingerprint=None,
            ),
            [item],
            cursor=Cursor(1, 1),
        )
    assert unavailable.value.code == "SEARCH_SEMANTIC_UNAVAILABLE"
    assert unavailable.value.trace == Trace("trace", "request")
