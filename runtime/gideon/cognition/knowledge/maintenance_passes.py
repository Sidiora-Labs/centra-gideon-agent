"""Lazy adapters and registration policy for the standing knowledge upkeep jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    from gideon.integrations.memory_providers.base import MemoryProvider

logger = logging.getLogger(__name__)
PASS_MEMORY_LINT = "memory_lint"
PASS_CONSOLIDATION = "knowledge_consolidation"
PASS_LINK_BACKFILL = "entity_link_backfill"
PASS_SIMILARITY_EDGES = "similarity_edges"
PASS_DERIVED_REFRESH = "derived_refresh"
PASS_VAULT_PROJECTION = "knowledge_vault_projection"


def _memory_lint_pass(*, batch_size: int = 0) -> int:
    from gideon.cognition import memory_service
    from gideon.cognition.memory import MemoryJournal

    service = memory_service.service_for(cast("MemoryProvider", MemoryJournal()))
    report = service.lint() if service is not None else None
    return (
        sum(len(value) for value in report.values() if isinstance(value, list))
        if isinstance(report, dict)
        else 0
    )


def _consolidation_pass(*, batch_size: int = 0) -> int:
    from gideon.integrations.action_providers import knowledge_maintain_provider

    run = knowledge_maintain_provider.run_consolidation_pass
    return run(batch_size=batch_size)


def _link_backfill_pass(*, batch_size: int = 0) -> int:
    from gideon.cognition.knowledge import link_backfill

    requested = batch_size or link_backfill.BATCH_SIZE
    return link_backfill.link_backfill_pass(batch_size=requested)


def _similarity_edge_pass(*, batch_size: int = 0) -> int:
    from gideon.cognition.knowledge import similarity_edges

    options = {"batch_size": batch_size} if batch_size > 0 else {}
    return similarity_edges.similarity_pass(**options)


def _derived_refresh_pass(*, batch_size: int = 0) -> int:
    from gideon.cognition.knowledge import (
        chunk_backfill,
        get_knowledge_embedder,
        get_knowledge_store,
    )

    embedder = get_knowledge_embedder()
    if embedder is None:
        return 0
    store = get_knowledge_store()
    limit = batch_size if batch_size > 0 else chunk_backfill.BATCH_SIZE
    chunks = chunk_backfill.backfill_item_chunks(store, embedder, max_items=limit)
    progressed = int(chunks.get("chunked") or 0)
    vectors = store.reembed_all(embedder, only_missing=True, limit=limit)
    return progressed + int(vectors.get("reembedded") or 0)


def _vault_projection_pass(*, batch_size: int = 0) -> int:
    from gideon.cognition.knowledge import vault

    project = vault.projection_pass
    return project(batch_size=batch_size)


@dataclass(frozen=True)
class _StandingJob:
    name: str
    run: Callable[..., int]
    resumable: bool

    def register(self, registry) -> bool:
        try:
            registry.register_pass(self.name, self.run, batched=self.resumable)
        except Exception:
            logger.warning(
                "maintenance pass %r not registered", self.name, exc_info=True
            )
            return False
        return True


def register_all() -> list[str]:
    from gideon.cognition.knowledge import maintenance

    jobs = (
        _StandingJob(PASS_MEMORY_LINT, _memory_lint_pass, False),
        _StandingJob(PASS_CONSOLIDATION, _consolidation_pass, False),
        _StandingJob(PASS_LINK_BACKFILL, _link_backfill_pass, True),
        _StandingJob(PASS_SIMILARITY_EDGES, _similarity_edge_pass, True),
        _StandingJob(PASS_DERIVED_REFRESH, _derived_refresh_pass, True),
        _StandingJob(PASS_VAULT_PROJECTION, _vault_projection_pass, True),
    )
    return [job.name for job in jobs if job.register(maintenance)]
