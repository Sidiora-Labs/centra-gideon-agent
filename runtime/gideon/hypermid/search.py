from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from .models import Cursor, Scope, Trace


_TOKEN = re.compile(r"[\w]+", re.UNICODE)


class SearchError(RuntimeError):
    def __init__(self, code: str, message: str, trace: Trace) -> None:
        super().__init__(message)
        self.code = code
        self.trace = trace


class SearchMode(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class Provenance:
    source_kind: str
    source_digest: str
    observed_at_ms: int
    capture_method: str
    source_id: str | None = None
    locator: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateVector:
    values: tuple[float, ...]
    fingerprint: str
    input_digest: str


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    id: str
    owner_scope: Scope
    source: str
    kind: str
    category: str
    content: str
    content_digest: str
    status: str = "active"
    expires_at_ms: int | None = None
    source_time_ms: int | None = None
    importance: float = 0.5
    verification: str = "unverified"
    provenance: tuple[Provenance, ...] = ()
    contradiction_group: str | None = None
    readable_scopes: frozenset[Scope] = frozenset()
    vector: CandidateVector | None = None
    decay: float = 0.0
    useful_count: int = 0
    not_useful_count: int = 0


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    scope: Scope
    mode: SearchMode
    limit: int
    trace: Trace
    now_ms: int
    candidate_limit_per_source: int = 100
    include_archived: bool = False
    visible_digests: frozenset[str] = frozenset()
    query_vector: tuple[float, ...] | None = None
    vector_fingerprint: str | None = None
    semantic_required: bool = False
    from_ms: int | None = None
    to_ms: int | None = None
    sources: frozenset[str] = frozenset()
    kinds: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.query or len(self.query) > 32768:
            raise ValueError("query must contain 1 to 32768 characters")
        if not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if not 1 <= self.candidate_limit_per_source <= 1000:
            raise ValueError("candidate_limit_per_source must be between 1 and 1000")


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    lexical: float
    semantic: float
    fusion: float
    importance: float
    recency: float
    verification: float
    provenance: float
    stability: float
    usefulness: float
    decay: float
    contradiction: float
    total: float


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: str
    scope: Scope
    source: str
    kind: str
    content: str
    content_digest: str
    scores: ScoreComponents
    verification: str
    contradiction_group: str | None
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True, slots=True)
class SuppressionCounts:
    unauthorized: int = 0
    state: int = 0
    stale: int = 0
    visible: int = 0
    duplicate: int = 0


@dataclass(frozen=True, slots=True)
class SearchResponse:
    hits: tuple[SearchHit, ...]
    cursor: Cursor
    suppressed: SuppressionCounts
    degraded: bool
    degradation_reason: str | None
    trace: Trace


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in _TOKEN.finditer(text))


def _lexical_scores(query: str, candidates: Sequence[SearchCandidate]) -> dict[str, float]:
    query_terms = set(_tokens(query))
    if not query_terms or not candidates:
        return {}
    documents = {candidate.id: Counter(_tokens(candidate.content)) for candidate in candidates}
    average_length = max(1.0, sum(sum(doc.values()) for doc in documents.values()) / len(documents))
    document_frequency = {
        term: sum(1 for doc in documents.values() if term in doc) for term in query_terms
    }
    scores: dict[str, float] = {}
    k1, b = 1.2, 0.75
    for candidate in candidates:
        document = documents[candidate.id]
        length = max(1, sum(document.values()))
        score = 0.0
        for term in query_terms:
            frequency = document[term]
            if not frequency:
                continue
            inverse_frequency = math.log(
                1.0 + (len(candidates) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            score += inverse_frequency * (frequency * (k1 + 1.0)) / (
                frequency + k1 * (1.0 - b + b * length / average_length)
            )
        if score > 0.0:
            scores[candidate.id] = score
    return scores


def _cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    if any(not math.isfinite(value) for value in (*left, *right)):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class SearchEngine:
    def __init__(self) -> None:
        self._explicit_retrievals: defaultdict[str, int] = defaultdict(int)

    def explicit_retrieval_count(self, candidate_id: str) -> int:
        return self._explicit_retrievals[candidate_id]

    def acknowledge_delivery(self, response: SearchResponse, *, automatic: bool) -> None:
        if automatic:
            return
        for hit in response.hits:
            self._explicit_retrievals[hit.id] += 1

    def search(
        self,
        request: SearchRequest,
        candidates: Iterable[SearchCandidate],
        *,
        cursor: Cursor,
    ) -> SearchResponse:
        collected = list(candidates)
        suppression = Counter()
        eligible: list[SearchCandidate] = []
        semantic_available = (
            request.query_vector is not None
            and request.vector_fingerprint is not None
            and _cosine(request.query_vector, request.query_vector) is not None
        )
        for candidate in collected:
            if candidate.owner_scope != request.scope and request.scope not in candidate.readable_scopes:
                suppression["unauthorized"] += 1
                continue
            if candidate.status in {"tombstoned", "stale"} or (
                candidate.status == "archived" and not request.include_archived
            ) or (candidate.expires_at_ms is not None and candidate.expires_at_ms <= request.now_ms):
                suppression["state"] += 1
                continue
            if request.from_ms is not None and candidate.source_time_ms is not None and candidate.source_time_ms < request.from_ms:
                suppression["state"] += 1
                continue
            if request.to_ms is not None and candidate.source_time_ms is not None and candidate.source_time_ms > request.to_ms:
                suppression["state"] += 1
                continue
            if request.sources and candidate.source not in request.sources:
                suppression["state"] += 1
                continue
            if request.kinds and candidate.kind not in request.kinds:
                suppression["state"] += 1
                continue
            if request.categories and candidate.category not in request.categories:
                suppression["state"] += 1
                continue
            if candidate.content_digest in request.visible_digests:
                suppression["visible"] += 1
                continue
            if request.mode is not SearchMode.LEXICAL and semantic_available and candidate.vector is not None:
                vector = candidate.vector
                if (
                    vector.input_digest != candidate.content_digest
                    or vector.fingerprint != request.vector_fingerprint
                    or _cosine(request.query_vector or (), vector.values) is None
                ):
                    suppression["stale"] += 1
                    continue
            eligible.append(candidate)

        lexical = _lexical_scores(request.query, eligible)
        semantic: dict[str, float] = {}
        if request.mode is not SearchMode.LEXICAL and semantic_available:
            for candidate in eligible:
                vector = candidate.vector
                if vector is None:
                    continue
                similarity = _cosine(request.query_vector or (), vector.values)
                assert similarity is not None
                semantic[candidate.id] = max(0.0, similarity)

        degraded = request.mode is not SearchMode.LEXICAL and not semantic_available
        if degraded and (request.semantic_required or request.mode is SearchMode.SEMANTIC):
            raise SearchError(
                "SEARCH_SEMANTIC_UNAVAILABLE",
                "semantic retrieval requires a compatible query vector",
                request.trace,
            )

        lexical_order = sorted(lexical, key=lambda item: (-lexical[item], item))
        semantic_order = sorted(semantic, key=lambda item: (-semantic[item], item))
        lexical_rank = {item: rank for rank, item in enumerate(lexical_order, 1)}
        semantic_rank = {item: rank for rank, item in enumerate(semantic_order, 1)}
        by_id = {candidate.id: candidate for candidate in eligible}
        ranked: list[SearchHit] = []
        seen_digests: set[str] = set()
        source_counts: Counter[str] = Counter()
        candidate_ids = set(lexical)
        if request.mode is not SearchMode.LEXICAL and not degraded:
            candidate_ids.update(semantic)
        for candidate_id in sorted(candidate_ids):
            candidate = by_id[candidate_id]
            if source_counts[candidate.source] >= request.candidate_limit_per_source:
                continue
            source_counts[candidate.source] += 1
            if candidate.content_digest in seen_digests:
                suppression["duplicate"] += 1
                continue
            seen_digests.add(candidate.content_digest)
            lexical_component = lexical.get(candidate_id, 0.0)
            semantic_component = semantic.get(candidate_id, 0.0)
            fusion = (0.65 / (60 + lexical_rank[candidate_id]) if candidate_id in lexical_rank else 0.0) + (
                0.35 / (60 + semantic_rank[candidate_id]) if candidate_id in semantic_rank else 0.0
            )
            importance = max(0.0, min(candidate.importance, 1.0)) * 0.08
            age_ms = max(0, request.now_ms - (candidate.source_time_ms or request.now_ms))
            recency = math.exp(-age_ms / (30 * 24 * 60 * 60 * 1000)) * 0.04
            verification = {"supported": 0.06, "disputed": -0.04, "refuted": -0.12}.get(candidate.verification, 0.0)
            provenance = min(len(candidate.provenance), 3) * 0.02
            stability = 0.05 if candidate.kind == "anchor" else 0.0
            usefulness = max(-0.03, min(0.03, (candidate.useful_count - candidate.not_useful_count) * 0.005))
            decay = -max(0.0, min(candidate.decay, 1.0)) * 0.08
            contradiction = -0.04 if candidate.contradiction_group is not None else 0.0
            total = fusion + importance + recency + verification + provenance + stability + usefulness + decay + contradiction
            ranked.append(
                SearchHit(
                    id=candidate.id,
                    scope=candidate.owner_scope,
                    source=candidate.source,
                    kind=candidate.kind,
                    content=candidate.content,
                    content_digest=candidate.content_digest,
                    scores=ScoreComponents(
                        lexical=lexical_component,
                        semantic=semantic_component,
                        fusion=fusion,
                        importance=importance,
                        recency=recency,
                        verification=verification,
                        provenance=provenance,
                        stability=stability,
                        usefulness=usefulness,
                        decay=decay,
                        contradiction=contradiction,
                        total=total,
                    ),
                    verification=candidate.verification,
                    contradiction_group=candidate.contradiction_group,
                    provenance=candidate.provenance,
                )
            )
        ranked.sort(key=lambda hit: (-hit.scores.total, hit.content_digest, hit.id))
        return SearchResponse(
            hits=tuple(ranked[: request.limit]),
            cursor=cursor,
            suppressed=SuppressionCounts(**{key: suppression[key] for key in SuppressionCounts.__dataclass_fields__}),
            degraded=degraded,
            degradation_reason="semantic_unavailable" if degraded else None,
            trace=request.trace,
        )
