"""Resumable similarity graph derivation from bounded chunk-neighbour searches."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.55
DEFAULT_CANDIDATE_MULTIPLE = 4
DEFAULT_DEGREE_CAP = 32
BATCH_SIZE = 10
_CONFIG_FIELDS = {
    "top_k": "similarity_top_k",
    "min_score": "similarity_min_score",
    "degree_cap": "similarity_degree_cap",
}
_DEFAULTS: dict[str, Any] = dict(
    top_k=DEFAULT_TOP_K,
    min_score=DEFAULT_MIN_SCORE,
    candidate_multiple=DEFAULT_CANDIDATE_MULTIPLE,
    degree_cap=DEFAULT_DEGREE_CAP,
)


def _open_store() -> Any:
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _resolve_tuning(**overrides: Any) -> dict[str, Any]:
    try:
        from gideon.core.config.loader import AppConfig

        section = AppConfig.load().knowledge
    except Exception:
        section = None
        logger.debug(
            "similarity edges: config unavailable, using defaults", exc_info=True
        )
    values = dict(_DEFAULTS)
    for key, name in _CONFIG_FIELDS.items():
        configured = getattr(section, name, None) if section is not None else None
        if section is not None and configured is None:
            logger.debug(
                "similarity edges: config field %s absent, using default %r",
                name,
                values[key],
            )
        candidates = (overrides.get(key), configured, values[key])
        values[key] = next(value for value in candidates if value is not None)
    return dict(
        top_k=max(0, int(values["top_k"])),
        min_score=float(values["min_score"]),
        candidate_multiple=max(1, int(values["candidate_multiple"])),
        degree_cap=max(0, int(values["degree_cap"])),
    )


@dataclass
class _NeighbourScores:
    minimum: float
    best: dict[str, tuple[float, Any, Any]] = field(default_factory=dict)

    def consider(self, own: dict, other: dict) -> None:
        from gideon.cognition.knowledge.dedup import cosine_similarity

        left, right = own["embedding"], other["embedding"]
        if not right or len(left) != len(right):
            return
        score = cosine_similarity(left, right)
        if score < self.minimum:
            return
        identifier = other["item_id"]
        previous = self.best.get(identifier)
        if previous is None or score > previous[0]:
            self.best[identifier] = (
                score,
                own.get("chunk_index"),
                other["chunk_index"],
            )

    def ranked(self, limit: int):
        return sorted(self.best.items(), key=lambda pair: (-pair[1][0], pair[0]))[
            :limit
        ]


@dataclass(frozen=True)
class _EdgeDerivation:
    store: Any
    item_id: str
    chunks: list[dict]
    top_k: int
    min_score: float
    candidate_multiple: int
    degree_cap: int

    def indexed(self, scores: _NeighbourScores) -> bool:
        from gideon.cognition.knowledge.embedder import floats_to_bytes

        index = getattr(self.store, "vec_index", None)
        if index is None or not getattr(index, "enabled", False):
            return False
        budget = max(1, self.top_k * self.candidate_multiple) + len(self.chunks)
        for chunk in self.chunks:
            vector = chunk["embedding"]
            identifiers = index.candidate_chunk_ids(
                floats_to_bytes(vector), len(vector), budget
            )
            if identifiers is None:
                return False
            for row in self.store.chunk_vectors_by_ids(identifiers):
                if row["item_id"] != self.item_id:
                    scores.consider(chunk, row)
        return True

    def execute(self) -> int:
        scores = _NeighbourScores(self.min_score)
        if not self.indexed(scores):
            scores.best.clear()
            for row in self.store.iter_embedded_chunks(exclude_item_id=self.item_id):
                for chunk in self.chunks:
                    scores.consider(chunk, row)
        selected = scores.ranked(self.top_k)
        claims = {
            tuple(sorted((self.item_id, identifier))) for identifier, _ in selected
        }
        rows = [
            dict(
                source_item_id=self.item_id,
                target_item_id=identifier,
                score=score,
                source_chunk_index=own,
                target_chunk_index=other,
                claimed_by=self.item_id,
            )
            for identifier, (score, own, other) in selected
        ]
        self.store.release_similarity_claims(self.item_id, claims)
        written = self.store.upsert_similarity_edges(rows)
        touched = [self.item_id] + [identifier for identifier, _ in selected]
        self.store.enforce_similarity_degree_cap(touched, self.degree_cap)
        return written


def recompute_item_edges(
    store: Any,
    item_id: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    candidate_multiple: int = DEFAULT_CANDIDATE_MULTIPLE,
    degree_cap: int = DEFAULT_DEGREE_CAP,
) -> int:
    chunks = [
        row
        for row in store.get_chunks(item_id, with_embedding=True) or []
        if row.get("embedding")
    ]
    if top_k <= 0 or not chunks:
        return 0
    return _EdgeDerivation(
        store, item_id, chunks, top_k, min_score, candidate_multiple, degree_cap
    ).execute()


def _close_owned_store(store: Any) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.debug("similarity edges: store close failed", exc_info=True)


def count_similarity_backlog() -> int:
    try:
        store = _open_store()
    except Exception:
        logger.debug("similarity edges: knowledge store unavailable", exc_info=True)
        return 0
    try:
        return int(store.count_items_missing_similarity_sweep())
    except Exception:
        logger.debug("similarity edges: backlog count failed", exc_info=True)
        return 0
    finally:
        _close_owned_store(store)


@dataclass
class _SimilarityBatch:
    store: Any
    tuning: dict[str, Any]
    processed: int = 0
    edges: int = 0

    def visit(self, identifier: str) -> None:
        try:
            self.edges += recompute_item_edges(self.store, identifier, **self.tuning)
        except Exception:
            logger.debug(
                "similarity edges: recompute failed for %s", identifier, exc_info=True
            )
        try:
            self.store.record_similarity_sweep(identifier)
        except Exception:
            logger.debug(
                "similarity edges: sweep marker failed for %s",
                identifier,
                exc_info=True,
            )
        else:
            self.processed += 1

    def execute(self, limit: int) -> int:
        try:
            if self.store.count_items_with_embedded_chunks() < 2:
                return 0
            identifiers = self.store.items_missing_similarity_sweep(limit=limit)
            if not identifiers:
                return 0
            for identifier in identifiers:
                if identifier:
                    self.visit(identifier)
        except Exception:
            logger.debug("similarity edges: batch failed", exc_info=True)
            return 0
        if self.processed:
            logger.debug(
                "similarity edges: swept %d item(s), writing %d edge(s)",
                self.processed,
                self.edges,
            )
        return self.processed


def similarity_pass(
    *,
    batch_size: int = BATCH_SIZE,
    top_k: int | None = None,
    min_score: float | None = None,
    candidate_multiple: int | None = None,
    degree_cap: int | None = None,
) -> int:
    limit = max(0, int(batch_size))
    if not limit:
        return 0
    try:
        store = _open_store()
    except Exception:
        logger.debug("similarity edges: knowledge store unavailable", exc_info=True)
        return 0
    try:
        tuning = _resolve_tuning(
            top_k=top_k,
            min_score=min_score,
            candidate_multiple=candidate_multiple,
            degree_cap=degree_cap,
        )
        return _SimilarityBatch(store, tuning).execute(limit)
    finally:
        _close_owned_store(store)
