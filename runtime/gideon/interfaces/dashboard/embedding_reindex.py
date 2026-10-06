"""Embedding re-index jobs with live progress.

Switching the active embedding model invalidates every stored vector — they came
from a different model and live in a different space (often a different
dimension). This module re-indexes both embedding stores as a background job
with SSE progress, mirroring :mod:`gideon.interfaces.dashboard.model_downloads`:

  * **Knowledge** — ``KnowledgeStore`` items and chunks (clear vectors → re-embed
    each from preserved title/summary/content and chunk text).
  * **Episodic memory** — ``SemanticArchive`` episodic rows (clear → re-embed
    from preserved text → rebuild FAISS). Semantic memory embeds lazily at query
    time, so clearing is enough there.

A single job at a time (re-indexing twice concurrently would race the stores);
``start`` returns the running job if one is already in flight. Progress frames
publish on the per-job SSE hub keyed ``reindex:<id>``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from gideon.interfaces.dashboard.sse import SseRegistry

logger = logging.getLogger(__name__)


def registry_key(job_id: str) -> str:
    """The SSE hub key for a re-index job's progress stream."""
    return f"reindex:{job_id}"


@dataclass
class ReindexJob:
    """One embedding re-index — identity, lifecycle, and progress.

    ``status`` is the coarse lifecycle (``running`` → ``done`` / ``error``);
    ``phase`` is the human-facing step. ``done``/``total`` count items processed
    across both stores so the UI can show a determinate bar.
    """

    id: str
    model: str
    status: str = "running"
    phase: str = "queued"
    done: int = 0
    total: int = 0
    knowledge: int = 0
    knowledge_chunks: int = 0
    memory: int = 0
    stale_index_reasons: list[dict[str, Any]] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "knowledge": self.knowledge,
            "knowledge_chunks": self.knowledge_chunks,
            "memory": self.memory,
            "stale_index_reasons": self.stale_index_reasons or [],
            "error": self.error,
        }


@dataclass
class _Running:
    job: ReindexJob
    task: asyncio.Task | None = None  # type: ignore[type-arg]


class ReindexRegistry:
    """Owns embedding re-index jobs + their per-job SSE progress streams.

    At most one job runs at a time. Finished jobs are retained so a re-attaching
    client (page reload) sees the terminal state.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ReindexJob] = {}
        self._running: dict[str, _Running] = {}
        self._sse = SseRegistry()
        self._counter = 0

    @property
    def sse(self) -> SseRegistry:
        return self._sse

    def _next_id(self) -> str:
        self._counter += 1
        return f"reindex-{self._counter}"

    def get(self, job_id: str) -> ReindexJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[ReindexJob]:
        return list(self._jobs.values())

    def active(self) -> ReindexJob | None:
        """The currently-running job, if any."""
        for run in self._running.values():
            if run.job.status == "running":
                return run.job
        return None

    def start(
        self,
        model: str,
        knowledge_store: Any,
        vector_store: Any,
        embedder: Any,
        embed_fn: Any,
        vector_stores: tuple[Any, ...] | list[Any] | None = None,
    ) -> tuple[ReindexJob | None, str | None]:
        """Begin a re-index (or return the in-flight one).

        ``embedder`` (knowledge, exposes ``embed_for_item``) and ``embed_fn``
        (memory, ``str -> list[float] | None``) must already be resolved from the
        NEW active model — the caller gates on availability before calling here.
        """
        running = self.active()
        if running is not None:
            return running, None

        job = ReindexJob(id=self._next_id(), model=model)
        self._jobs[job.id] = job
        run = _Running(job=job)
        self._running[job.id] = run
        stores = tuple(vector_stores) if vector_stores is not None else ((vector_store,) if vector_store is not None else ())
        run.task = asyncio.ensure_future(
            self._drive(run, knowledge_store, stores, embedder, embed_fn)
        )
        return job, None

    def _publish(self, job: ReindexJob, event: str) -> None:
        self._sse.publish(registry_key(job.id), event, job.to_dict())

    async def _drive(
        self,
        run: _Running,
        knowledge_store: Any,
        vector_stores: tuple[Any, ...],
        embedder: Any,
        embed_fn: Any,
    ) -> None:
        job = run.job
        try:
            k_total = 0
            if knowledge_store and embedder is not None:
                from gideon.cognition.knowledge.pipeline.runner import (
                    embedding_space_fingerprint,
                )

                dimension = getattr(embedder, "dim", None)
                try:
                    dimension = dimension() if callable(dimension) else dimension
                except Exception:
                    dimension = None

                k_total = knowledge_store.count_items_to_reembed(
                    *embedding_space_fingerprint(embedder), dimension
                )
                provider, model = embedding_space_fingerprint(embedder)
                k_total += knowledge_store.count_chunks_to_reembed(
                    provider, model, dimension
                )
            from gideon.hypermid.memory import HypermidMemoryProvider
            native_stores = tuple(store for store in vector_stores if isinstance(store, HypermidMemoryProvider))
            legacy_stores = tuple(store for store in vector_stores if store is not None and not isinstance(store, HypermidMemoryProvider))
            m_total = sum(store.count_episodic_to_reembed() for store in legacy_stores)
            job.total = k_total + m_total
            job.phase = "clearing"
            self._publish(job, "progress")

            await asyncio.to_thread(
                self._reindex_sync,
                run,
                knowledge_store,
                legacy_stores,
                embedder,
                embed_fn,
            )
            for store in native_stores:
                job.phase = "reindexing native memory"
                self._publish(job, "progress")
                base_done, base_total = job.done, job.total
                def progress(done, total):
                    job.done = base_done + done
                    job.total = base_total + total
                    self._publish(job, "progress")
                result = await store.embedding_reembed_all(on_progress=progress)
                job.memory += result["reembedded"]
                job.total = base_total + result["total"]
                job.done = base_done + result["reembedded"]
                if result["skipped"]:
                    raise RuntimeError("Native embedding backfill skipped changed records; retry to complete.")

            job.status = "done"
            job.phase = "done"
            self._publish(job, "done")
        except asyncio.CancelledError:
            job.status = "error"
            job.phase = "cancelled"
            job.error = "Embedding re-index was cancelled; existing records and vectors were preserved."
            self._publish(job, "error")
            raise
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            logger.warning("Embedding re-index failed: %s", exc, exc_info=True)
            job.status = "error"
            job.phase = "error"
            job.error = str(exc)[:300]
            self._publish(job, "error")
        finally:
            self._running.pop(job.id, None)

    def _reindex_sync(
        self,
        run: _Running,
        knowledge_store: Any,
        vector_stores: tuple[Any, ...],
        embedder: Any,
        embed_fn: Any,
    ) -> None:
        """Blocking re-index of both stores. Publishes throttled progress frames."""
        job = run.job

        def _progress(done_in_phase: int, base: int) -> None:
            job.done = base + done_in_phase
            self._publish(job, "progress")

        k_done = 0
        if knowledge_store and embedder is not None:
            from gideon.cognition.knowledge.pipeline.runner import (
                embedding_space_fingerprint,
            )

            provider, model = embedding_space_fingerprint(embedder)
            dim_fn = getattr(embedder, "dim", None)
            try:
                active_dim = dim_fn() if callable(dim_fn) else None
            except Exception:
                active_dim = None
            fingerprint = (provider, model)
            before = knowledge_store.chunk_embedding_status(provider, model, active_dim)
            job.stale_index_reasons = list(before.get("reasons") or [])
            job.phase = "reindexing knowledge items and chunks"
            self._publish(job, "progress")
            res = knowledge_store.reembed_all(
                embedder,
                on_progress=lambda d, _t: _progress(d, 0),
                embedding_fingerprint=fingerprint,
                embedding_dimension=active_dim,
            )
            job.knowledge = res.get("reembedded", 0)
            k_done = res.get("total", 0)
            chunk_offset = k_done
            while True:
                chunk_result = knowledge_store.reembed_stale_chunks(
                    embedder,
                    limit=100,
                    on_progress=lambda d, _t: _progress(d, chunk_offset),
                )
                chunk_total = int(chunk_result.get("total", 0))
                reembedded = int(chunk_result.get("reembedded", 0))
                job.knowledge_chunks += reembedded
                chunk_offset += chunk_total
                if chunk_total == 0 or reembedded == 0:
                    break
            k_done = chunk_offset
            remaining_items = knowledge_store.count_items_to_reembed(
                provider, model, active_dim
            )
            remaining_chunks = knowledge_store.count_chunks_to_reembed(
                provider, model, active_dim
            )
            if remaining_items or remaining_chunks:
                raise RuntimeError(
                    "embedding re-index left rows outside the active embedding space"
                )

        memory_offset = k_done
        for store_index, vector_store in enumerate(vector_stores):
            if vector_store is None:
                continue
            job.phase = "reindexing memory"
            self._publish(job, "progress")
            vector_store.embed_fn = embed_fn
            res = vector_store.reembed_all(
                on_progress=lambda d, _t: _progress(d, memory_offset)
            )
            job.memory += res.get("reembedded", 0)
            memory_offset += int(res.get("total", 0))


CHUNK_BACKFILL_PASS = "chunk_backfill"


def chunk_backfill_pass(*, batch_size: int) -> int:
    """One bounded batch of the knowledge CHUNK backfill (KL-12). Returns units processed.

    Sibling of the item-vector re-index above, and deliberately separate from it: the
    re-index rewrites the items' OWN vectors on a model switch, while this only adds the
    chunk layer beneath them for items that predate chunking. Both are needed and neither
    substitutes for the other.

    This is a graph-maintenance pass, not a boot hook (KL-14). The backfill used to run from
    ``app.on_startup``, which fires exactly once: on a gateway that stays up for a week, a
    library that gains pre-chunking items after boot never gained deep-document recall. The
    host (:mod:`gideon.cognition.knowledge.maintenance`) instead calls this on every due tick and
    keeps claiming sub-batches until it returns 0, so the backlog drains across ticks and the
    store lock is released between batches.

    Cheap when there is nothing to do: the backlog is derived from the rows (see
    ``store.count_items_missing_chunks``), so a fully-chunked library costs one COUNT and
    returns 0 — checked BEFORE resolving an embedder, because resolving one probes the
    provider over the network and a no-op pass must not pay for that every tick.

    Faults propagate: the host isolates a failing pass (``fatal=False``) and records it in
    ``MaintenanceResult.errors``, which is strictly more legible than swallowing here. Only
    the deferred cases (empty backlog, no embedding model bound) return 0.
    """
    from gideon.cognition.knowledge import get_knowledge_store

    store = get_knowledge_store()
    if store.count_items_missing_chunks() <= 0:
        return 0

    from gideon.cognition.knowledge.chunk_backfill import BATCH_SIZE as _FETCH_BATCH
    from gideon.cognition.knowledge.chunk_backfill import backfill_item_chunks
    from gideon.interfaces.dashboard.handlers.embedding_reindex import _resolve_embed

    embedder, _embed_fn, _model = _resolve_embed(None)
    if embedder is None:
        logger.info(
            "Knowledge chunk backfill pending: item(s) need chunking but no embedding "
            "model is ready — deep-document recall resumes once one is bound."
        )
        return 0

    result = backfill_item_chunks(
        store,
        embedder,
        batch_size=min(int(batch_size), _FETCH_BATCH),
        max_items=int(batch_size),
    )
    return int(result.get("done", 0))


def register_chunk_backfill_pass() -> None:
    """Register the chunk backfill with the graph-maintenance host.

    Registration is boot-time and free (it stores a callable); the WORK is what moved to the
    tick. Idempotent — the registry is keyed by name, so registering twice replaces rather
    than appends.
    """
    from gideon.cognition.knowledge import maintenance

    maintenance.register_pass(CHUNK_BACKFILL_PASS, chunk_backfill_pass)
