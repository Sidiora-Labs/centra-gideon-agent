"""Workflow retrieval with explicit query, search-ladder and evidence projection owners."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from gideon.cognition.knowledge.semantics import freshness, logical_key, normalize_title
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
MAX_TOP_K = 25
DEFAULT_TOP_K = 5
DETAIL_CAPS: dict[str, int] = {"brief": 0, "compact": 600, "full": 4_000}
RELEVANCE_CLIFF = 0.30
PROBABLE_RANK = 2
_ITEM_COLUMNS = (
    "id",
    "title",
    "summary",
    "content",
    "kind",
    "updated_at",
    "last_verified",
    "expires_at",
    "file_metadata",
)
_METADATA_COLUMNS = _ITEM_COLUMNS[4:]


@dataclass(frozen=True)
class _QuerySpec:
    query: str
    detail: str
    limit: int
    filters: dict[str, Any]
    mode: str

    @classmethod
    def parse(cls, config: dict[str, Any]) -> _QuerySpec:
        query = str(config.get("query") or "").strip()
        if not query:
            raise ValueError(
                "knowledge-retrieve is missing 'query' — bind it to a node's output"
            )
        detail = str(config.get("detail") or "compact")
        if detail not in DETAIL_CAPS:
            raise ValueError(
                f"detail {detail!r} must be one of: {', '.join(DETAIL_CAPS)}"
            )
        filters = config.get("filters")
        return cls(
            query,
            detail,
            _bounded_top_k(config.get("top_k")),
            filters if isinstance(filters, dict) else {},
            str(config.get("mode", "semantic")),
        )

    def retrieve(self, store: Any) -> dict[str, Any]:
        hits, strategy = _search(store, self.query, top_k=self.limit, mode=self.mode)
        admitted = _apply_filters(_enrich(store, hits), self.filters, strategy=strategy)
        selected = [
            _shape_hit(store, hit, query=self.query, detail=self.detail, rank=rank)
            for rank, hit in enumerate(admitted[: self.limit])
        ]
        overview = _overview_for(store, self.query, detail=self.detail)
        if overview is not None:
            selected = [overview] + [
                item for item in selected if item["item_id"] != overview["item_id"]
            ]
        omitted = len(admitted) - self.limit
        if not selected:
            logger.info("knowledge coverage gap for query %r", self.query[:120])
        return dict(
            items=selected,
            strategy=strategy,
            truncated=f"{omitted} older items not consulted" if omitted > 0 else None,
            coverage_gap=not selected,
        )


@dataclass(frozen=True)
class _ItemRows:
    store: Any

    def select(
        self,
        columns: tuple[str, ...],
        source: str,
        predicate: str,
        parameters: tuple[Any, ...],
        *,
        alias: str = "",
    ) -> list[dict]:
        names = ", ".join(f"{alias}.{name}" if alias else name for name in columns)
        statement = f"SELECT {names} FROM {source} WHERE {predicate}"
        try:
            return [dict(row) for row in self.store.db.execute(statement, parameters)]
        except Exception:
            logger.debug("knowledge retrieval query unavailable", exc_info=True)
            return []


@dataclass(frozen=True)
class _SearchLadder:
    store: Any
    query: str
    limit: int

    def hybrid(self) -> list[dict]:
        try:
            from gideon.cognition.knowledge.retrieval import HybridRetriever

            return HybridRetriever(self.store, embedder=_embedder()).search(
                self.query, limit=self.limit
            )
        except Exception:
            logger.debug("hybrid retrieval unavailable — degrading", exc_info=True)
            return []

    def answer(self, mode: str) -> tuple[list[dict], str]:
        if mode == "fts":
            return _fts(self.store, self.query, limit=self.limit), "fts"
        for strategy, search in (
            ("hybrid", self.hybrid),
            ("fts_fallback", lambda: _fts(self.store, self.query, limit=self.limit)),
            (
                "substring_fallback",
                lambda: _substring(self.store, self.query, limit=self.limit),
            ),
        ):
            rows = search()
            if rows:
                return rows, strategy
        return [], "none"


def _search(store, query: str, *, top_k: int, mode: str) -> tuple[list[dict], str]:
    return _SearchLadder(store, query, max(10, 3 * top_k)).answer(mode)


def _embedder():
    try:
        from gideon.integrations.embedding_providers.registry import get_active_embed_fn

        return get_active_embed_fn()
    except Exception:
        return None


def _fts(store, query: str, *, limit: int) -> list[dict]:
    try:
        expression = store._sanitize_fts5(query)
    except Exception:
        logger.debug(
            "FTS sanitizer unavailable — skipping the keyword rung", exc_info=True
        )
        return []
    if not expression:
        return []
    rows = _ItemRows(store).select(
        _ITEM_COLUMNS,
        "items_fts f JOIN items i ON i.rowid = f.rowid",
        "items_fts MATCH ? LIMIT ?",
        (expression, limit),
        alias="i",
    )
    return [{**row, "score": 0.5, "match_type": "keyword"} for row in rows]


def _like_escape(text: str) -> str:
    return "".join(
        "\\" + character if character in "\\%_" else character for character in text
    )


def _substring(store, query: str, *, limit: int) -> list[dict]:
    pattern = "%" + _like_escape(query.strip()) + "%"
    rows = _ItemRows(store).select(
        _ITEM_COLUMNS,
        "items",
        "title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' LIMIT ?",
        (pattern, pattern, limit),
    )
    return [{**row, "score": 0.35, "match_type": "substring"} for row in rows]


def _bounded_top_k(raw: Any) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_TOP_K
    return min(MAX_TOP_K, max(1, raw))


def _enrich(store, hits: list[dict]) -> list[dict]:
    records = _ItemRows(store)
    metadata: dict[str, dict] = {}
    enriched = []
    for hit in hits:
        identifier = str(hit.get("id") or "")
        if hit.get("kind") is not None or not identifier:
            enriched.append(hit)
            continue
        if identifier not in metadata:
            rows = records.select(
                _METADATA_COLUMNS, "items", "id = ? LIMIT 1", (identifier,)
            )
            metadata[identifier] = rows[0] if rows else {}
        enriched.append({**hit, **metadata[identifier]})
    return enriched


def _apply_filters(
    hits: list[dict], filters: dict[str, Any], *, strategy: str
) -> list[dict]:
    kind = str(filters.get("kind") or "").strip().lower()

    def accepted(hit: dict) -> bool:
        if kind and str(hit.get("kind") or "").lower() != kind:
            return False
        return strategy == "hybrid" or float(hit.get("score", 0.0)) >= RELEVANCE_CLIFF

    return [hit for hit in hits if accepted(hit)]


def _passage_window(content: str, line_range: Any, cap: int) -> tuple[str, bool]:
    if not cap:
        return "", False
    head = content[:cap], False
    if not isinstance(line_range, (list, tuple)) or len(line_range) != 2:
        return head
    try:
        start, end = map(int, line_range)
    except (ValueError, TypeError):
        return head
    if start < 1 or end < start or end > content.count("\n") + 1:
        return head
    offset = 0
    for _ in range(max(0, start - 2)):
        offset = content.index("\n", offset) + 1
    return ("…" if offset else "") + content[offset:][:cap], True


@dataclass(frozen=True)
class _HitProjection:
    hit: dict
    query: str
    detail: str
    rank: int

    def render(self) -> dict[str, Any]:
        hit = self.hit
        title = str(hit.get("title") or "")
        exact = normalize_title(title) == normalize_title(self.query)
        evidence = (
            "exact_title"
            if exact
            else _evidence_for(str(hit.get("match_type") or "vector"))
        )
        location = hit.get("line_range")
        body, windowed = _passage_window(
            str(hit.get("content") or ""), location, DETAIL_CAPS[self.detail]
        )
        metadata = _meta(hit)
        claims = metadata.get("claims")
        support = (
            max(
                (
                    int(claim.get("support_count") or 0)
                    for claim in claims
                    if isinstance(claim, dict)
                ),
                default=0,
            )
            if isinstance(claims, list)
            else 0
        )
        result = {
            "item_id": str(hit.get("id") or ""),
            "title": title,
            "summary": str(hit.get("summary") or ""),
            "content": body,
            "section": hit.get("section"),
            "line_range": (
                list(location) if isinstance(location, (list, tuple)) else None
            ),
            "content_windowed": windowed,
            "kind": str(hit.get("kind") or ""),
            "logical_key": logical_key(str(hit.get("kind") or "fact"), title),
            "relevance_score": round(float(hit.get("score") or 0.0), 4),
            "evidence": evidence,
            "create_safety": _create_safety(
                exact=exact, rank=self.rank, evidence=evidence
            ),
            "freshness": freshness(
                **{
                    name: str(hit.get(name) or "")
                    for name in ("updated_at", "last_verified", "expires_at")
                }
            ).to_dict(),
            "support_count": support,
            "read_when": metadata.get("read_when") or [],
        }
        return result


def _shape_hit(
    store, hit: dict, *, query: str, detail: str, rank: int = 0
) -> dict[str, Any]:
    return _HitProjection(hit, query, detail, rank).render()


def _evidence_for(match_type: str) -> str:
    available = (match_type or "").lower()
    return next(
        (
            tier
            for tier in ("vector", "graph", "keyword", "substring")
            if tier in available
        ),
        "vector",
    )


def _create_safety(*, exact: bool, rank: int, evidence: str) -> str:
    if exact or evidence == "exact_title":
        return "exists"
    strong = evidence in {"vector", "graph", "keyword"} and rank < PROBABLE_RANK
    return "probable" if strong else "unknown"


def _overview_for(store, query: str, *, detail: str) -> dict[str, Any] | None:
    key = logical_key("overview", query)
    if not key:
        return None
    rows = _ItemRows(store).select(
        _ITEM_COLUMNS, "items", "logical_key = ? LIMIT 1", (key,)
    )
    if not rows:
        return None
    return _shape_hit(
        store,
        {**rows[0], "score": 1.0, "match_type": "exact_title"},
        query=query,
        detail=detail,
        rank=0,
    )


def _meta(hit: dict) -> dict[str, Any]:
    try:
        value = json.loads(hit.get("file_metadata") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _open_store():
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


class KnowledgeRetrieveActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-retrieve"

    @property
    def display_name(self) -> str:
        return "Retrieve Knowledge"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        started = time.monotonic()
        try:
            request = _QuerySpec.parse(action_config or {})
        except ValueError as exc:
            return ActionResult(False, error=str(exc))
        try:
            store = _open_store()
        except Exception as exc:
            return ActionResult(False, error=f"knowledge store unavailable: {exc}")
        try:
            payload = request.retrieve(store)
        finally:
            try:
                store.close()
            except Exception:
                logger.debug("knowledge retrieval store close failed", exc_info=True)
        return ActionResult(
            True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
