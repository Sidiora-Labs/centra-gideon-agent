"""HybridRetriever -- FTS5 keyword + graph + optional vector, fused with RRF."""

import asyncio
import concurrent.futures
import json
import logging
import math
import re
import struct
from collections import defaultdict
from dataclasses import dataclass

from gideon.cognition.knowledge.embedder import floats_to_bytes
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.sqlite_compat import sqlite3

logger = logging.getLogger(__name__)

_RELEVANCE_CLIFF_GAP = 0.30
_CLIFF_MIN_RESULTS = 1

_VECTOR_MIN_SIMILARITY = 0.25

_TITLE_BOOST = 1.0 / 61

_ANN_OVERFETCH = 4
_ANN_ESCALATION_FACTOR = 4
_ANN_MAX_ATTEMPTS = 4

_ID_BATCH = 400

_DEFAULT_RERANKER_MAX_CANDIDATES = 32

ARM_KEYWORD = "keyword"
ARM_GRAPH = "graph"
ARM_VECTOR = "vector"
ARMS = (ARM_KEYWORD, ARM_GRAPH, ARM_VECTOR)

RANKING_SCORE_LABEL = "Ranking score"
RANKING_SCORE_KIND = "relative_ordering_signal"
RANKING_SCORE_EXPLANATION = (
    "A relative ordering signal for this query, not confidence, probability, or a "
    "percentage. Scores from different queries or ranking methods are not comparable."
)

_RANKING_SIGNAL_LABELS = {
    "keyword": "Keyword match",
    "vector": "Meaning match",
    "graph": "Entity links",
    "title": "Title match",
    "recency": "Recency",
    "importance": "Importance",
    "prior_use": "Prior use",
    "diversity": "Diversity",
    "contributor": "Contributor ownership",
}


def ranking_signal(
    signal_id: str,
    detail: str,
    *,
    active: bool = True,
    applies_to: tuple[str, ...] = (),
) -> dict:
    """Describe one ranking input using the API/UI recall vocabulary."""
    return {
        "id": signal_id,
        "label": _RANKING_SIGNAL_LABELS[signal_id],
        "active": active,
        "applies_to": list(applies_to),
        "detail": detail,
    }


def ranking_disclosure(
    method: str,
    summary: str,
    signals: list[dict],
    *,
    score: float | None = None,
) -> dict:
    """Return machine-readable semantics for an otherwise opaque ranking score."""
    return {
        "method": method,
        "summary": summary,
        "score": {
            "label": RANKING_SCORE_LABEL,
            "kind": RANKING_SCORE_KIND,
            "value": score,
            "shown": score is not None,
            "is_probability": False,
            "comparable_across_queries": False,
            "explanation": RANKING_SCORE_EXPLANATION,
        },
        "signals": signals,
    }


@dataclass(frozen=True)
class RerankResult:
    enabled: bool
    applied: bool
    candidate_ids: tuple[str, ...] = ()
    reason: str = ""


class RelevanceReranker:
    """Post-fusion relevance ordering through the active reasoning model."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_candidates: int = _DEFAULT_RERANKER_MAX_CANDIDATES,
    ) -> None:
        self.enabled = bool(enabled)
        self.max_candidates = min(128, max(1, int(max_candidates)))

    @classmethod
    def from_config(cls) -> "RelevanceReranker":
        try:
            from gideon.core.config.loader import AppConfig

            config = AppConfig.load().knowledge
            return cls(
                enabled=config.rerank_enabled,
                max_candidates=config.rerank_max_candidates,
            )
        except Exception:  # noqa: BLE001 — retrieval remains available without config
            return cls(enabled=False)

    def rerank(self, query: str, candidates: list[dict]) -> RerankResult:
        """Return a validated model ordering, failing open on every unusable response."""
        bounded = candidates[: self.max_candidates]
        if not self.enabled or not bounded:
            return RerankResult(
                self.enabled,
                False,
                reason="disabled" if not self.enabled else "no_candidates",
            )
        prompt = (
            "Order these knowledge candidates by relevance to the query. Return only a JSON "
            "array of candidate IDs, most relevant first.\n"
            f"Query: {query[:2000]}\nCandidates:\n"
            + "\n".join(
                json.dumps(
                    {
                        "id": item["id"],
                        "title": item.get("title"),
                        "summary": item.get("summary"),
                        "content": str(item.get("content") or "")[:12000],
                    },
                    ensure_ascii=False,
                )
                for item in bounded
            )
        )
        try:
            from gideon.integrations.llm_helpers import one_shot_completion

            call = one_shot_completion(prompt, use_case="reasoning")
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                response = asyncio.run(call)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    response = pool.submit(asyncio.run, call).result()
            requested = json.loads(response)
            if not isinstance(requested, list):
                raise ValueError("response is not an ID array")
            real = {str(item["id"]) for item in bounded}
            ordered = tuple(
                dict.fromkeys(
                    str(item_id) for item_id in requested if str(item_id) in real
                )
            )
            if not ordered:
                raise ValueError("response names no candidate")
            return RerankResult(True, True, ordered)
        except Exception:  # noqa: BLE001 — assistant reasoning has a fail-open floor
            logger.debug("knowledge rerank unavailable", exc_info=True)
            return RerankResult(True, False, reason="unusable_response")


def relevance_cliff_cut(
    scores: list[float],
    *,
    min_results: int = _CLIFF_MIN_RESULTS,
    max_results: int | None = None,
    gap: float = _RELEVANCE_CLIFF_GAP,
) -> int:
    """Return how many leading results to keep, cutting at the relevance cliff.

    ``scores`` must be sorted descending. Walks consecutive pairs and cuts before
    the first where the drop exceeds ``gap`` × (top score) — the elbow between
    the relevant cluster and the weak tail. The result is clamped to
    ``[min_results, max_results or len(scores)]``; a degenerate top score of 0
    (no signal) keeps everything up to the cap. Pure + side-effect-free so the
    cutoff is unit-testable apart from the DB-backed ranking path.
    """
    n = len(scores)
    cap = n if max_results is None else min(max_results, n)
    if n <= 1:
        return cap
    top = scores[0]
    if top <= 0:
        return cap
    threshold = gap * top
    cut = n
    for i in range(1, n):
        if scores[i - 1] - scores[i] > threshold:
            cut = i
            break
    return max(min(min_results, cap), min(cut, cap))


class HybridRetriever:
    """FTS5 keyword + graph traversal + optional vector search, fused with RRF."""

    def __init__(self, store: KnowledgeStore, embedder=None, reranker=None):
        """Construct retrieval with optional embedding and relevance providers.

        ``reranker`` is an explicit injection seam for a configured provider. When omitted,
        the knowledge config constructs the shipped local-only cross encoder, which remains
        disabled by default.
        """
        self.store = store
        self.embedder = embedder
        self.vector_index_status: dict = {}
        self.stale_index_reasons: list[dict] = []
        self.reranker = reranker or RelevanceReranker.from_config()
        self.last_reranker_result = RerankResult(self.reranker.enabled, False)

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        include_archived: bool = False,
        arms: "tuple[str, ...] | list[str] | set[str] | None" = None,
    ) -> list[dict]:
        """Hybrid search with RRF fusion. Returns [{id, title, summary, content, score, source, match_type}].  # noqa: E501

        ``include_archived`` defaults False — archived items never surface to agents or
        chat context-injection. The Archived UI view sets it True so a search *within*
        that view can find archived items (matching the no-query Archived list).

        ``arms`` masks which of :data:`ARMS` contribute (EVALUATION-SUBSTRATE §5.1's
        ablation knob). ``None`` — the default every production caller uses — runs all
        three, so the live ranking is unchanged by construction. A masked arm is not
        merely dropped from fusion: its query is **never issued**, so an ablation cell
        measures the arm's absence rather than its cost. An empty mask is legal and
        returns ``[]``: that is the harness's control cell, and a control that came back
        with hits is how you learn the mask was not applied.
        """
        active = ARMS if arms is None else tuple(a for a in ARMS if a in set(arms))
        over = limit * 2
        kw = (
            self._keyword_search(query, limit=over, include_archived=include_archived)
            if ARM_KEYWORD in active
            else []
        )
        gr = (
            self._graph_search(query, limit=over, include_archived=include_archived)
            if ARM_GRAPH in active
            else []
        )
        chunk_locs: dict[str, dict] = {}
        vec = (
            self._vector_search(
                query,
                limit=over,
                include_archived=include_archived,
                chunk_locators=chunk_locs,
            )
            if ARM_VECTOR in active
            else []
        )

        fused = self._rrf_fuse(kw, gr, vec)

        all_ids = [item_id for item_id, _ in fused]
        items_cache: dict[str, dict] = {}
        for item_id in all_ids:
            item = self.store.get_item(item_id)
            if item:
                items_cache[item_id] = item

        q_terms = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}
        if q_terms:
            boosted = []
            for iid, sc in fused:
                title = (items_cache.get(iid, {}).get("title") or "").lower()
                t_terms = {t for t in re.findall(r"[a-z0-9]+", title) if len(t) > 1}
                if t_terms:
                    overlap = len(q_terms & t_terms) / len(q_terms)
                    sc += overlap * _TITLE_BOOST
                boosted.append((iid, sc))
            fused = boosted

        def _sort_key(item_score: tuple[str, float]) -> tuple[float, str]:
            item_id, score = item_score
            updated = items_cache.get(item_id, {}).get("updated_at", "")
            return (score, updated)

        fused.sort(key=_sort_key, reverse=True)

        keep = relevance_cliff_cut([score for _, score in fused], max_results=limit)

        rerank_input = [
            {"id": item_id, **items_cache[item_id]}
            for item_id, _ in fused
            if item_id in items_cache
        ]
        self.last_reranker_result = self.reranker.rerank(query, rerank_input)
        if self.last_reranker_result.applied:
            original_scores = dict(fused)
            reranked_ids = list(self.last_reranker_result.candidate_ids)
            reranked = set(reranked_ids)
            reranked_ids.extend(
                item_id for item_id, _ in fused if item_id not in reranked
            )
            fused = [(item_id, original_scores[item_id]) for item_id in reranked_ids]

        kw_ids = {i for i, _ in kw}
        gr_ids = {i for i, _ in gr}
        vec_ids = {i for i, _ in (vec or [])}

        results = []
        for item_id, score in fused[:keep]:
            item = items_cache.get(item_id)
            if not item:
                continue
            types = []
            if item_id in kw_ids:
                types.append("keyword")
            if item_id in gr_ids:
                types.append("graph")
            if item_id in vec_ids:
                types.append("vector")
            result = {
                "id": item_id,
                "title": item["title"],
                "summary": item.get("summary"),
                "content": item["content"],
                "score": score,
                "provider": item.get("provider", "native"),
                "match_type": "+".join(types),
                **_attach_locator(item, q_terms, chunk_locs.get(item_id)),
            }
            result["ranking"] = ranking_disclosure(
                "reciprocal_rank_fusion",
                "Keyword, entity-link, and meaning-match result positions are combined; "
                "title matches can add a small ordering boost, a relevance cliff can remove "
                "the weak tail, and an enabled reranker may reorder candidates.",
                [
                    ranking_signal(
                        ARM_KEYWORD,
                        "Ranks literal word and prefix matches.",
                        active=ARM_KEYWORD in active,
                    ),
                    ranking_signal(
                        ARM_GRAPH,
                        "Ranks documents connected to entities named in the query.",
                        active=ARM_GRAPH in active,
                    ),
                    ranking_signal(
                        ARM_VECTOR,
                        "Ranks embedding similarity when an embedder is available.",
                        active=ARM_VECTOR in active and self.embedder is not None,
                    ),
                    ranking_signal(
                        "title",
                        "Adds a small boost for query words found in the title.",
                        active=bool(q_terms),
                    ),
                ],
                score=score,
            )
            if self.last_reranker_result.applied:
                result["match_type"] += "+reranker"
            results.append(result)
        return results

    def _keyword_search(
        self, query: str, limit: int = 20, *, include_archived: bool = False
    ) -> list[tuple[str, int]]:
        """FTS5 search. Returns [(item_id, rank)] where rank is position (1=best)."""
        safe_query = self._sanitize_fts5_query(query)
        if not safe_query:
            return []
        archived_clause = (
            "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
        )
        try:
            rows = self.store.db.execute(
                "SELECT i.id FROM items_fts fts "
                "JOIN items i ON i.rowid = fts.rowid "
                "WHERE items_fts MATCH ? AND i.status = 'active' "
                f"{archived_clause}ORDER BY fts.rank LIMIT ?",  # noqa: S608,E501 (clause is a fixed literal)
                (safe_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(row["id"], rank + 1) for rank, row in enumerate(rows)]

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Escape user input for FTS5 MATCH, OR-ing prefix-matched terms.

        OR (not the default implicit AND) so a conversational query
        ("how do we store refresh tokens") still matches docs that contain only
        some terms — RRF + rank then float the best overlap to the top. Each term
        is a ``"term"*`` prefix match so "token" also hits "tokens"/"tokenize".
        """
        terms = [t.replace('"', '""') for t in query.split() if t]
        return " OR ".join(f'"{t}"*' for t in terms)

    def _graph_search(
        self, query: str, limit: int = 20, *, include_archived: bool = False
    ) -> list[tuple[str, int]]:
        """Find entities matching query terms, traverse graph, rank items by mention count."""
        words = query.split()
        candidates = list(words)
        for size in (2, 3):
            for i in range(len(words) - size + 1):
                candidates.append(" ".join(words[i : i + size]))
        if len(words) > 1:
            candidates.append(query.strip())

        entity_ids = set()
        for term in candidates:
            ent = self.store.find_entity(term)
            if ent:
                entity_ids.add(ent["id"])

        if not entity_ids:
            return []

        all_entity_ids = set(entity_ids)
        for eid in entity_ids:
            for neighbor in self.store.get_neighbors(eid, depth=2):
                all_entity_ids.add(neighbor["id"])

        item_counts: dict[str, int] = defaultdict(int)
        placeholders = ",".join("?" * len(all_entity_ids))
        archived_clause = (
            "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
        )
        rows = self.store.db.execute(
            f"SELECT m.item_id, COUNT(*) as cnt FROM mentions m "  # noqa: S608
            f"JOIN items i ON i.id = m.item_id "
            f"WHERE m.entity_id IN ({placeholders}) AND i.status = 'active' "
            f"{archived_clause}"
            f"GROUP BY m.item_id ORDER BY cnt DESC LIMIT ?",
            (*all_entity_ids, limit),
        ).fetchall()
        for row in rows:
            item_counts[row["item_id"]] = row["cnt"]

        sorted_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)
        return [(item_id, rank + 1) for rank, (item_id, _) in enumerate(sorted_items)]

    def _vector_search(
        self,
        query: str,
        limit: int = 20,
        *,
        include_archived: bool = False,
        chunk_locators: dict[str, dict] | None = None,
    ) -> list[tuple[str, int]] | None:
        """Brute-force cosine similarity over CHUNK vectors *and* whole-item vectors,
        rolled up to one score per item. Returns None if no embedder.

        The return type is deliberately still ``[(item_id, rank)]`` — the fusion contract.
        Chunks are an indexing detail that must not leak into ``_rrf_fuse``: a chunk hit is
        rolled up to its parent item BEFORE ranking, so the fused arm sees exactly the list
        shape it always saw and fusion needs no change at all (KNOWLEDGE-LIBRARY §Risks:
        "do not redesign fusion").

        **Roll-up rule: MAX.** An item's vector score is the single best above-floor
        similarity found for it, across its chunk vectors and its own whole-item vector.
        Max, not mean or sum, because:
        - the question retrieval asks is "does this document contain the answer", which is
          a max over passages — a mean drags a 50-chunk document with one perfect passage
          below a 2-chunk document with two mediocre ones, and a sum simply rewards length
          (the very bias the title boost below exists to counteract in BM25);
        - max is the only aggregate that leaves the score on the *identical* scale as the
          old item-level cosine, so ``_VECTOR_MIN_SIMILARITY`` keeps its calibrated
          meaning. Any averaging aggregate would silently re-scale that threshold, and
          retuning a threshold is out of scope for this task (escalation E6).

        Because the whole-item scan is kept unchanged and merely maxed against the chunk
        scan, an item with no chunk rows (or whose chunks are not embedded yet, mid-backfill)
        contributes only its whole-item vector: the fallback is a *consequence* of the max
        rather than a special case, so a partially-chunked library degrades in ranking
        quality and never loses an item. A chunked item also keeps its title+summary
        signal, which no chunk carries and which the keyword arm can only reach by literal
        term match.

        **KL-11: the scan is a fallback, not the plan.** When ``sqlite-vec`` loads, the chunk
        arm asks a ``vec0`` index for the k nearest chunk vectors and the item arm orders by
        ``vec_distance_cosine``, so neither arm reads every BLOB into Python. Both are pure
        candidate generation — ``_consider`` still scores — so the exact scan and the ANN path
        share one scoring implementation and cannot disagree on a similarity value. When the
        extension cannot load (a SQLite built without loadable extensions), both arms revert to
        the streamed exact scan above: slower on a large library, identical in what it returns,
        announced once at INFO and reported by the Doctor.

        ``chunk_locators`` is an optional sink: when supplied, it is filled with
        ``item_id -> {"section", "line_start", "line_end"}`` for every item whose winning
        signal was a chunk, so ``search`` can cite the passage that actually matched. It is
        an out-parameter rather than part of the return value precisely so the ranked list
        handed to fusion cannot drift.
        """
        self.vector_index_status = {}
        self.stale_index_reasons = []
        if self.embedder is None:
            return None

        query_vec = self.embedder(query)
        if not query_vec:
            return None
        q_dim = len(query_vec)
        from gideon.cognition.knowledge.pipeline.runner import (
            embedding_space_fingerprint,
        )

        embedding_provider, embedding_model = embedding_space_fingerprint(self.embedder)
        self.vector_index_status = self.store.chunk_embedding_status(
            embedding_provider, embedding_model, q_dim
        )
        self.stale_index_reasons = list(self.vector_index_status.get("reasons") or [])

        best: dict[str, float] = {}
        best_loc: dict[str, dict] = {}

        from gideon.integrations.vector_store_providers.registry import get_provider

        external_store = get_provider()
        chunk_archived = (
            "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0"
        )
        chunk_cols = "SELECT c.id AS chunk_id, c.item_id, c.embedding, c.section, c.line_start, c.line_end "

        def _consider(item_id: str, blob, locator: dict | None) -> float | None:
            """Score one vector into the roll-up. Returns the similarity, or ``None`` when the
            vector is unscoreable (dimension guard). The value is returned — not just applied —
            so the ANN candidate loop can see where the ``_VECTOR_MIN_SIMILARITY`` floor falls
            in an ordered candidate list without a second cosine implementation."""
            vec = _bytes_to_floats(blob)
            if not vec or len(vec) != q_dim:
                return None
            sim = self._cosine_similarity(query_vec, vec)
            if sim < _VECTOR_MIN_SIMILARITY:
                return sim
            if sim > best.get(item_id, -1.0):
                best[item_id] = sim
                if locator is None:
                    best_loc.pop(item_id, None)
                else:
                    best_loc[item_id] = locator
            return sim

        if external_store is not None:
            try:
                candidates = external_store.search(
                    list(query_vec), limit=max(1, limit) * _ANN_OVERFETCH
                )
                previous = 1.0
                candidate_ids: list[str] = []
                candidate_scores: dict[str, float] = {}
                for hit in candidates:
                    similarity = float(hit["similarity"])
                    if not math.isfinite(similarity) or not -1.0 <= similarity <= 1.0:
                        raise ValueError(
                            "vector-store similarity must be finite and in [-1, 1]"
                        )
                    if similarity > previous:
                        raise ValueError("vector-store results must be descending")
                    previous = similarity
                    if similarity < _VECTOR_MIN_SIMILARITY:
                        break
                    chunk_id = str(hit["chunk_id"])
                    candidate_ids.append(chunk_id)
                    candidate_scores[chunk_id] = similarity
                for start in range(0, len(candidate_ids), _ID_BATCH):
                    batch = candidate_ids[start : start + _ID_BATCH]
                    placeholders = ",".join("?" * len(batch))
                    for row in self.store.db.execute(
                        chunk_cols + "FROM chunks c JOIN items i ON i.id = c.item_id "
                        f"WHERE c.id IN ({placeholders}) AND i.status = 'active' "  # noqa: S608
                        f"{chunk_archived}",
                        batch,
                    ):
                        similarity = candidate_scores[row["chunk_id"]]
                        item_id = row["item_id"]
                        if similarity > best.get(item_id, -1.0):
                            best[item_id] = similarity
                            best_loc[item_id] = {
                                "section": row["section"],
                                "line_start": row["line_start"],
                                "line_end": row["line_end"],
                            }
            except Exception:
                logger.warning("External vector-store search failed", exc_info=True)
                return []

            scored = sorted(best.items(), key=lambda x: (-x[1], x[0]))[:limit]
            if chunk_locators is not None:
                for item_id, _ in scored:
                    loc = best_loc.get(item_id)
                    if loc is not None:
                        chunk_locators[item_id] = loc
            return [(item_id, rank + 1) for rank, (item_id, _) in enumerate(scored)]

        index = getattr(self.store, "vec_index", None)
        if index is not None and not index.enabled:
            index = None
        q_blob = floats_to_bytes(query_vec) if index is not None else b""
        chunk_index = index if not self.vector_index_status.get("stale") else None

        def _consider_chunk_row(row) -> float | None:
            return _consider(
                row["item_id"],
                row["embedding"],
                {
                    "section": row["section"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                },
            )

        ann_served = False
        if chunk_index is not None:
            k = max(1, limit) * _ANN_OVERFETCH
            seen: set[str] = set()
            for _ in range(_ANN_MAX_ATTEMPTS):
                cand = chunk_index.candidate_chunk_ids(q_blob, q_dim, k)
                if cand is None:
                    break
                ann_served = True
                fresh = [cid for cid in cand if cid not in seen]
                seen.update(fresh)
                reached_floor = False
                for start in range(0, len(fresh), _ID_BATCH):
                    batch = fresh[start : start + _ID_BATCH]
                    placeholders = ",".join("?" * len(batch))
                    rows_by_id = {
                        row["chunk_id"]: row
                        for row in self.store.db.execute(
                            chunk_cols
                            + "FROM chunks c JOIN items i ON i.id = c.item_id "
                            f"WHERE c.id IN ({placeholders}) "  # noqa: S608 (placeholders only)
                            "AND c.embedding IS NOT NULL AND i.status = 'active' "
                            "AND COALESCE(c.embedding_provider, '') = ? "
                            "AND COALESCE(c.embedding_model, '') = ? "
                            f"{chunk_archived}",
                            (*batch, embedding_provider, embedding_model),
                        )
                    }
                    for chunk_id in batch:
                        row = rows_by_id.get(chunk_id)
                        if row is None:
                            continue
                        sim = _consider_chunk_row(row)
                        if sim is not None and sim < _VECTOR_MIN_SIMILARITY:
                            reached_floor = True
                            break
                    if reached_floor:
                        break
                if reached_floor or len(best) >= limit or len(cand) < k:
                    break
                k *= _ANN_ESCALATION_FACTOR

        if not ann_served:
            for row in self.store.db.execute(
                chunk_cols + "FROM chunks c JOIN items i ON i.id = c.item_id "
                "WHERE c.embedding IS NOT NULL AND i.status = 'active' "
                "AND COALESCE(c.embedding_provider, '') = ? "
                "AND COALESCE(c.embedding_model, '') = ? "
                f"{chunk_archived}",  # noqa: S608 (clause is a fixed literal)
                (embedding_provider, embedding_model),
            ):
                _consider_chunk_row(row)

        archived_clause = "" if include_archived else "AND COALESCE(is_archived, 0) = 0"
        item_rows = None
        if index is not None:
            try:
                item_rows = self.store.db.execute(
                    "SELECT id, embedding FROM items "
                    "WHERE embedding IS NOT NULL AND status = 'active' "
                    f"{archived_clause} AND length(embedding) = ? "  # noqa: S608
                    "ORDER BY vec_distance_cosine(embedding, ?) LIMIT ?",
                    (q_dim * 4, q_blob, max(1, limit) * _ANN_OVERFETCH),
                ).fetchall()
            except (
                sqlite3.Error
            ) as exc:  # fail soft to the exact scan, never into the search
                logger.debug(
                    "knowledge vector search: item-arm ANN query failed: %s", exc
                )
                item_rows = None
        if item_rows is not None:
            for row in item_rows:
                sim = _consider(row["id"], row["embedding"], None)
                if sim is not None and sim < _VECTOR_MIN_SIMILARITY:
                    break
        else:
            for row in self.store.db.execute(
                "SELECT id, embedding FROM items WHERE embedding IS NOT NULL "
                f"AND status = 'active' {archived_clause}"  # noqa: S608 (fixed literal)
            ):
                _consider(row["id"], row["embedding"], None)

        scored = sorted(best.items(), key=lambda x: (-x[1], x[0]))[:limit]
        if chunk_locators is not None:
            for item_id, _ in scored:
                loc = best_loc.get(item_id)
                if loc is not None:
                    chunk_locators[item_id] = loc
        return [(item_id, rank + 1) for rank, (item_id, _) in enumerate(scored)]

    @staticmethod
    def _rrf_fuse(*ranked_lists, k: int = 60) -> list[tuple[str, float]]:
        """Reciprocal Rank Fusion across all non-None ranked lists."""
        scores: dict[str, float] = defaultdict(float)
        for rlist in ranked_lists:
            if rlist is None:
                continue
            for item_id, rank in rlist:
                scores[item_id] += 1.0 / (k + rank)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity. Returns 0.0 for zero vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


def _bytes_to_floats(blob: bytes) -> list[float]:
    """Decode an embedding blob of ``struct``-packed 32-bit floats."""
    if not blob:
        return []
    if isinstance(blob, bytes) and len(blob) >= 16 and len(blob) % 4 == 0:
        try:
            n = len(blob) // 4
            return list(struct.unpack(f"{n}f", blob))
        except struct.error:
            pass
    return []


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _attach_locator(
    item: dict, q_terms: set[str], chunk_locator: dict | None = None
) -> dict:
    """Return the four citation fields for a result: ``source_type`` (the item kind),
    ``section`` (nearest structural header / slide / sheet above the best match, or None),
    ``line_range`` (1-based [start,end] of the best-matching line span in ``content``, or
    None), and ``deep_link`` (``/knowledge/items/{id}?loc=…``). Pure: reads only the item
    dict, the already-computed query terms, and the optional winning-chunk locator; no DB,
    no I/O.

    With ``chunk_locator`` the result is never LESS specific than without it: the term scan
    is narrowed to the winning chunk's lines but keeps its identical ±1-line window width,
    and when no query term appears literally inside that passage — the pure-semantic case
    that yields a null locator today — the chunk's own span and heading are cited instead
    of nothing.
    """
    iid = item.get("id") or ""
    source_type = str(item.get("item_type") or "").strip() or "item"
    content = item.get("content") or ""
    lines = content.split("\n") if content else []

    lo, hi = 0, len(lines) - 1
    chunk_span: list[int] | None = None
    if chunk_locator:
        c_start, c_end = chunk_locator.get("line_start"), chunk_locator.get("line_end")
        if (
            isinstance(c_start, int)
            and isinstance(c_end, int)
            and 1 <= c_start <= len(lines)
        ):
            lo = c_start - 1
            hi = max(lo, min(len(lines) - 1, c_end - 1))
            chunk_span = [lo + 1, hi + 1]

    best_line = -1
    best_hits = 0
    if q_terms and lines:
        for i in range(lo, hi + 1):
            toks = {t for t in re.findall(r"[a-z0-9]+", lines[i].lower()) if len(t) > 1}
            hits = len(q_terms & toks)
            if hits > best_hits:
                best_hits, best_line = hits, i

    section: str | None = None
    line_range: list[int] | None = None
    if best_line >= 0 and best_hits > 0:
        start = max(lo, best_line - 1)
        end = min(hi, best_line + 1)
        line_range = [start + 1, end + 1]
        for j in range(best_line, -1, -1):
            m = _HEADER_RE.match(lines[j])
            if m:
                section = m.group(2).strip()[:120]
                break
    elif chunk_span is not None:
        line_range = chunk_span

    if section is None and chunk_locator and chunk_locator.get("section"):
        section = str(chunk_locator["section"]).strip()[:120] or None

    if section is None:
        fmeta = item.get("file_metadata") or {}
        if isinstance(fmeta, dict) and fmeta.get("page_count"):
            section = None

    loc = f"L{line_range[0]}-{line_range[1]}" if line_range else ""
    deep_link = f"/knowledge/items/{iid}" + (f"?loc={loc}" if loc else "")

    return {
        "source_type": source_type,
        "section": section,
        "line_range": line_range,
        "deep_link": deep_link,
    }
