"""Whether an ingested item can actually be FOUND, and when it cannot, why.

An ingest that finishes without raising is not the same fact as an item a search can
return, and the store had no way to say so: an item whose extraction yielded nothing,
an item that never reached the keyword index, and an item on an install with no
embedding provider all ended ``processing_status='done'`` and then silently failed to
appear in results. The user's only evidence was an absence.

So a fourth terminal state — :data:`STATE_UNSEARCHABLE` — is assigned when the finished
item has NO retrieval reach at all, carrying a TYPED reason for which prerequisite was
the one missing. The reason is a ladder, first unmet wins, because the causes stack: an
item with no text has no vector either, and reporting the second would send the reader
after an embedding model for an item that had nothing to embed.

The state is measured, never inferred. :func:`assess` asks the FTS index whether it can
return this item for this item's own terms, and reads the item's own vector column —
both real queries against the live store, so an index that exists but does not hold the
row is recorded as the miss it is rather than assumed to be a hit.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

STATE_UNSEARCHABLE = "unsearchable"

REASON_NO_CONTENT = "no_searchable_content"
REASON_NO_EMBEDDING_PROVIDER = "no_embedding_provider"
REASON_NO_INDEX = "no_usable_index"

UNSEARCHABLE_REASONS = (
    REASON_NO_CONTENT,
    REASON_NO_EMBEDDING_PROVIDER,
    REASON_NO_INDEX,
)

REASON_DETAIL = {
    REASON_NO_CONTENT: (
        "unsearchable: ingestion extracted no searchable content "
        "(nothing to index and nothing to embed)"
    ),
    REASON_NO_EMBEDDING_PROVIDER: (
        "unsearchable: the keyword index cannot return this item and no embedding "
        "provider is configured, so it has no vector either — connect an embedding "
        "model in Settings → Models"
    ),
    REASON_NO_INDEX: (
        "unsearchable: no usable index holds this item — the keyword index cannot "
        "return it and no vector was written"
    ),
}

REASON_REMEDY = {
    REASON_NO_CONTENT: (
        "Re-ingest these items from a source that carries text, or add a title and "
        "body by hand — there is nothing for either index to hold."
    ),
    REASON_NO_EMBEDDING_PROVIDER: (
        "Connect an embedding model in Settings → Models, then re-ingest: a vector "
        "reaches content the keyword index cannot."
    ),
    REASON_NO_INDEX: (
        "Rebuild the knowledge index (Settings → Knowledge → Reindex), then re-ingest "
        "these items."
    ),
}

METADATA_KEY = "unsearchable_reason"

_TERM_RE = re.compile(r"[A-Za-z0-9]+")

_MAX_PROBE_TERMS = 12


def _probe_terms(item: dict) -> list[str]:
    """The item's own indexable terms, as the FTS tokenizer would see them.

    Drawn from the same three columns ``items_fts`` indexes (title/content/tags) plus the
    summary, because a summary-only item is still findable. Empty means the item carries
    nothing a keyword index could ever match, whatever the index's state.
    """
    parts = [
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
        str(item.get("content") or ""),
    ]
    tags = item.get("tags")
    if isinstance(tags, (list, tuple)):
        parts.extend(str(tag) for tag in tags)
    seen: list[str] = []
    for part in parts:
        for term in _TERM_RE.findall(part):
            if term not in seen:
                seen.append(term)
            if len(seen) >= _MAX_PROBE_TERMS:
                return seen
    return seen


def keyword_reach(store, item_id: str, terms: list[str]) -> tuple[bool, bool]:
    """``(item_is_returned, index_is_usable)`` for one item against the live FTS index.

    A real ``MATCH`` for the item's own terms, narrowed to the item's rowid — the only
    honest test, because an external-content FTS5 table answers a bare ``WHERE rowid = ?``
    out of the CONTENT table and would report a hit for a row the index never took.

    ``index_is_usable`` is False only when the index itself could not be consulted (the
    virtual table is missing or unreadable). A usable index that simply does not hold the
    row returns ``(False, True)``, which is a different diagnosis and a different remedy.
    """
    if not terms:
        return False, True
    expression = " OR ".join(f'"{term}"*' for term in terms)
    try:
        row = store.db.execute(
            "SELECT rowid FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return False, True
        rowid = row["rowid"] if not isinstance(row, tuple) else row[0]
        hit = store.db.execute(
            "SELECT 1 FROM items_fts WHERE items_fts MATCH ? AND rowid = ? LIMIT 1",
            (expression, rowid),
        ).fetchone()
    except Exception:  # noqa: BLE001 — an unreadable index is a diagnosis, not a crash
        logger.debug("keyword reach probe failed for %s", item_id, exc_info=True)
        return False, False
    return hit is not None, True


def has_vector(store, item_id: str) -> bool:
    """Whether the item carries an embedding the vector arm can score."""
    try:
        row = store.db.execute(
            "SELECT embedding FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    except Exception:  # noqa: BLE001
        logger.debug("vector presence probe failed for %s", item_id, exc_info=True)
        return False
    if row is None:
        return False
    blob = row["embedding"] if not isinstance(row, tuple) else row[0]
    return bool(blob)


def assess(store, item_id: str, *, embedding_provider: bool) -> str:
    """The typed unsearchable reason for *item_id*, or ``""`` when it can be found.

    ``embedding_provider`` says whether an embedder was available to this ingest at all —
    not whether it produced a vector. The distinction is the whole point of the second
    rung: an install with no embedding model and an embedder that ran and wrote nothing
    are the same absence of a vector but different problems, and only the first is fixed
    by connecting a model.
    """
    item = store.get_item(item_id)
    if not item:
        return ""
    if has_vector(store, item_id):
        return ""
    terms = _probe_terms(item)
    if not terms:
        return REASON_NO_CONTENT
    reachable, index_usable = keyword_reach(store, item_id, terms)
    if reachable:
        return ""
    if not index_usable:
        return REASON_NO_INDEX
    if not embedding_provider:
        return REASON_NO_EMBEDDING_PROVIDER
    return REASON_NO_INDEX


def reason_of(item: dict) -> str:
    """The typed reason recorded on an item, or ``""``."""
    metadata = (item or {}).get("file_metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            return ""
    if not isinstance(metadata, dict):
        return ""
    reason = str(metadata.get(METADATA_KEY) or "")
    return reason if reason in UNSEARCHABLE_REASONS else ""


def summary(store) -> dict:
    """``{"count": n, "reasons": {reason: n}}`` over the store's unsearchable items.

    The shape the search API and the Doctor both report: a total a caller can branch on
    without parsing, and the per-reason split that says which remedy applies. An item
    whose typed reason is missing or unrecognised is counted under
    :data:`REASON_NO_CONTENT`'s neighbour ``"unknown"`` rather than dropped — a state
    recorded without a reason is still a state the user is entitled to see.
    """
    counts: dict[str, int] = {}
    total = 0
    try:
        rows = store.db.execute(
            "SELECT file_metadata FROM items "
            "WHERE status = 'active' AND processing_status = ?",
            (STATE_UNSEARCHABLE,),
        ).fetchall()
    except Exception:  # noqa: BLE001 — diagnostics must never break a search
        logger.debug("unsearchable summary failed", exc_info=True)
        return {"count": 0, "reasons": {}}
    for row in rows:
        raw = row["file_metadata"] if not isinstance(row, tuple) else row[0]
        reason = reason_of({"file_metadata": raw}) or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
        total += 1
    return {"count": total, "reasons": counts}
