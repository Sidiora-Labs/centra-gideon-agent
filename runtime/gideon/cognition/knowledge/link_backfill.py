"""Bounded entity mention sweeps with persistent per-item completion markers."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
BATCH_SIZE = 25


def _open_store() -> Any:
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    path = knowledge_db_path()
    return KnowledgeStore(db_path=str(path))


def _linkable_text(row: dict) -> str:
    values = (
        str(row.get(field) or "").strip() for field in ("title", "summary", "content")
    )
    return "\n\n".join(filter(None, values))


def _store_operation(operation):
    store = None
    try:
        store = _open_store()
        return operation(store)
    except Exception:
        logger.debug("Mention backfill store operation failed", exc_info=True)
        return 0
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                logger.debug("Mention backfill store close failed", exc_info=True)


def count_link_backlog() -> int:
    return _store_operation(
        lambda store: int(store.count_items_missing_mention_sweep())
    )


class _MentionBatch:
    def __init__(self, store):
        self.store = store
        self.processed = self.linked = 0

    def record(self, row, linker):
        item_id = row.get("id")
        if not item_id:
            return
        try:
            text = _linkable_text(row)
            if text:
                self.linked += int(linker(self.store, item_id, text) or 0)
        except Exception:
            logger.debug("Mention matching failed for %s", item_id, exc_info=True)
        try:
            self.store.record_mention_sweep(item_id)
        except Exception:
            logger.debug("Mention sweep marker failed for %s", item_id, exc_info=True)
        else:
            self.processed += 1

    def run(self, limit):
        if not _has_entities(self.store):
            return 0
        rows = self.store.items_missing_mention_sweep(limit=limit)
        if rows:
            from gideon.cognition.knowledge.alias_prepass import link_known_entities

            for row in rows:
                self.record(row, link_known_entities)
        if self.processed:
            logger.debug(
                "Mention backfill processed %d items, added %d links",
                self.processed,
                self.linked,
            )
        return self.processed


def link_backfill_pass(*, batch_size: int = BATCH_SIZE) -> int:
    limit = max(0, int(batch_size))
    return (
        _store_operation(lambda store: _MentionBatch(store).run(limit)) if limit else 0
    )


def _has_entities(store: Any) -> bool:
    try:
        row = store.db.execute("SELECT 1 FROM entities LIMIT 1").fetchone()
    except Exception:
        logger.debug("Mention backfill entity lookup failed", exc_info=True)
        return False
    return row is not None
