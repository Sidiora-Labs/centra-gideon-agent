"""Keyset-driven backfill of the additive chunk index."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)
BATCH_SIZE = 25


class _ChunkSweep:
    def __init__(self, store, embedder, total, progress):
        self.store, self.embedder, self.progress = store, embedder, progress
        self.cursor = None
        self.counts = dict(
            chunked=0, unchanged=0, failed=0, done=0, remaining=total, total=total
        )

    def consume(self, row, writer):
        item_id = self.cursor = row["id"]
        outcome = "failed"
        try:
            writer(self.store, item_id, row["content"], self.embedder)
            outcome = "chunked" if len(self.store.get_chunks(item_id)) else "unchanged"
        except Exception:
            logger.debug("Chunk backfill failed for %s", item_id, exc_info=True)
        self.counts[outcome] += 1
        self.counts["done"] += 1
        if self.progress is not None:
            self.progress(self.counts["done"], self.counts["total"])

    def run(self, budget, batch_size):
        from gideon.cognition.knowledge.pipeline.runner import embed_item_chunks

        while budget > self.counts["done"]:
            rows = self.store.items_missing_chunks(
                limit=min(int(batch_size), budget - self.counts["done"]),
                after_id=self.cursor,
            )
            if not rows:
                break
            for row in rows:
                self.consume(row, embed_item_chunks)
        self.counts["remaining"] = int(self.store.count_items_missing_chunks())
        return self.counts


def backfill_item_chunks(
    store: Any,
    embedder: Any,
    *,
    batch_size: int = BATCH_SIZE,
    max_items: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    total = int(store.count_items_missing_chunks())
    sweep = _ChunkSweep(store, embedder, total, on_progress)
    if total <= 0:
        return sweep.counts
    if not callable(getattr(embedder, "embed", None)):
        logger.info(
            "Chunk backfill deferred for %d items: embedding unavailable", total
        )
        sweep.counts["skipped_no_embedder"] = True
        return sweep.counts
    budget = total if max_items is None else min(total, max(0, int(max_items)))
    logger.info("Chunk backfill starting with %d pending items", total)
    result = sweep.run(budget, batch_size)
    logger.info("Chunk backfill completed: %s", result)
    return result
