"""Knowledge maintenance: inspect, propose a consolidation plan, or apply reviewed text."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from gideon.cognition.knowledge import consolidation
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
MAX_REPORTED_IDS = 50
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]{2,80})(?:\|[^\]]*)?\]\]")


@dataclass
class _MaintenanceRun:
    config: dict[str, Any]
    context: ActionContext
    started: float = field(default_factory=time.monotonic)

    async def execute(self, operation: str) -> ActionResult:
        try:
            store = _open_store()
        except Exception as exc:
            return ActionResult(
                success=False, error=f"knowledge store unavailable: {exc}"
            )
        try:
            items = _load_items(store)
            if operation == "health":
                payload = self.health(store, items)
            elif operation == "gaps":
                payload = self.gaps(items)
            else:
                payload = await self.consolidate(store, items)
            return ActionResult(
                success=True,
                stdout=json.dumps(payload, ensure_ascii=False),
                duration_ms=int((time.monotonic() - self.started) * 1000),
            )
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("maintenance connection close failed", exc_info=True)

    def health(self, store: Any, items: list[consolidation.Item]) -> dict[str, Any]:
        if not items:
            return {
                "report": consolidation.HealthReport().to_dict(),
                "item_count": 0,
                "note": "the store is empty — nothing to check",
            }
        report = consolidation.check_health(
            items,
            known_ids={item.id for item in items},
            indexed_ids=_indexed_ids(store),
            expired_ids=_expired_ids(store),
        )
        repaired = (
            _reindex(store, report.unindexed)
            if (_truthy(self.config.get("fix_index")) and report.unindexed)
            else []
        )
        categories = ("stubs", "orphans", "broken_citations", "expired", "unindexed")
        return {
            "report": _capped(report.to_dict()),
            "counts": {key: len(getattr(report, key)) for key in categories},
            "item_count": len(items),
            "clean": report.clean,
            "reindexed": repaired,
        }

    def gaps(self, items: list[consolidation.Item]) -> dict[str, Any]:
        hubs = consolidation.phantom_hubs(
            items,
            mentions=_wikilink_mentions(items),
            min_mentions=_int(self.config.get("min_mentions"), 3),
        )
        return {
            "gaps": hubs[:MAX_REPORTED_IDS],
            "count": len(hubs),
            "excerpts": {
                hub["entity"]: _excerpts_for(items, hub["referrers"], hub["entity"])
                for hub in hubs[:10]
            },
            "note": "These are candidates for PROPOSALS, not writes. A drafted entry nobody reviewed "
            "becomes a citable source for the next draft.",
        }

    async def consolidate(
        self, store: Any, items: list[consolidation.Item]
    ) -> dict[str, Any]:
        hours, size = _config_gate_defaults()
        gate = consolidation.check_gates(
            unprocessed=sum(
                not item.consolidated and not item.is_archived for item in items
            ),
            hours_since_last=_hours_since_last_pass(store),
            lock_held=False,
            min_new_items=_int(
                self.config.get("min_new_items"), consolidation.MIN_NEW_ITEMS
            ),
            min_hours=float(_int(self.config.get("min_hours"), hours)),
        )
        if not gate:
            return {"ran": False, "reason": gate.reason, "backlog": gate.backlog}
        plan = consolidation.plan_consolidation(
            items,
            similarity=_similarity_for(store),
            min_size=_int(self.config.get("min_cluster_size"), size),
        )
        summaries = self.config.get("summaries")
        payload = {"ran": True, "applied": False, "plan": plan.to_dict()}
        if (
            _truthy(self.config.get("apply"))
            and isinstance(summaries, list)
            and summaries
        ):
            count, issues = await _apply(store, plan, summaries, self.context)
            payload.update(applied=True, summaries_written=count, issues=issues)
        else:
            payload.update(
                doctrine=consolidation.CONSOLIDATION_DOCTRINE,
                prompts=[
                    {"cluster": n, "prompt": consolidation.synthesis_prompt(cluster)}
                    for n, cluster in enumerate(plan.clusters)
                ],
            )
        return payload


class KnowledgeHealthActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-health"

    @property
    def display_name(self) -> str:
        return "Check Knowledge Health"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        return await _MaintenanceRun(action_config or {}, ctx).execute("health")


class KnowledgeGapsActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-gaps"

    @property
    def display_name(self) -> str:
        return "Find Knowledge Gaps"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        return await _MaintenanceRun(action_config or {}, ctx).execute("gaps")


class KnowledgeConsolidateActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-consolidate"

    @property
    def display_name(self) -> str:
        return "Consolidate Knowledge"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 60
    ) -> ActionResult:
        return await _MaintenanceRun(action_config or {}, ctx).execute("consolidate")


def _wikilink_mentions(items: list[consolidation.Item]) -> dict[str, list[str]]:
    mentions: dict[str, list[str]] = defaultdict(list)
    for item in (entry for entry in items if not entry.is_archived):
        for entity in (
            match.group(1).strip() for match in _WIKILINK_RE.finditer(item.text)
        ):
            if entity:
                mentions[entity].append(item.id)
    return dict(mentions)


def _excerpts_for(
    items: list[consolidation.Item], referrer_ids: list[str], entity: str
) -> list[str]:
    bodies = {item.id: item.text for item in items}
    excerpts = []
    for identifier in referrer_ids[:5]:
        if identifier not in bodies:
            continue
        sentence = next(
            (
                part
                for part in re.split(r"(?<=[.!?])\s+", bodies[identifier])
                if entity.lower() in part.lower()
            ),
            None,
        )
        if sentence is not None:
            excerpts.append(f"[{identifier}] {sentence.strip()[:300]}")
    return excerpts


@dataclass(frozen=True)
class _MaintenanceRows:
    store: Any

    def items(self) -> list[consolidation.Item]:
        incoming = {}
        try:
            counts = self.store.db.execute(
                "SELECT target_item_id, COUNT(*) AS n FROM item_relations GROUP BY target_item_id"
            )
            incoming = {str(row["target_item_id"]): int(row["n"]) for row in counts}
        except Exception:
            logger.debug(
                "item_relations unavailable — skipping orphan detection", exc_info=True
            )
        try:
            rows = list(
                self.store.db.execute(
                    "SELECT id, kind, title, summary, content, logical_key, content_hash, "
                    "updated_at, is_archived, file_metadata FROM items"
                )
            )
        except Exception:
            logger.warning("could not read knowledge items", exc_info=True)
            return []
        result = []
        missing = 0 if incoming else 1
        for row in rows:
            item = consolidation.Item.from_row(row)
            item.inbound_relations = incoming.get(item.id, missing)
            result.append(item)
        return result

    def ids(self, query: str, values: tuple = ()) -> set[str]:
        return {str(row["id"]) for row in self.store.db.execute(query, values)}

    def restore(self, identifiers: list[str]) -> list[str]:
        repaired = []
        for identifier in identifiers[:MAX_REPORTED_IDS]:
            try:
                row = self.store.db.execute(
                    "SELECT rowid, title, content FROM items WHERE id = ?",
                    (identifier,),
                ).fetchone()
                if row is None:
                    continue
                values = (row["rowid"], row["title"] or "", row["content"] or "", "")
                self.store.db.execute(
                    "INSERT INTO items_fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                    values,
                )
                self.store.db.commit()
            except Exception:
                logger.warning("could not reindex %s", identifier, exc_info=True)
            else:
                repaired.append(identifier)
        return repaired


def _load_items(store: Any) -> list[consolidation.Item]:
    return _MaintenanceRows(store).items()


def _indexed_ids(store: Any) -> set[str] | None:
    try:
        return _MaintenanceRows(store).ids(
            "SELECT i.id AS id FROM items_fts f JOIN items i ON i.rowid = f.rowid"
        )
    except Exception:
        logger.debug("items_fts unreadable — skipping index consistency", exc_info=True)
        return None


def _expired_ids(store: Any) -> set[str]:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        return _MaintenanceRows(store).ids(
            "SELECT id FROM items WHERE expires_at != '' AND expires_at < ?", (stamp,)
        )
    except Exception:
        return set()


def _reindex(store: Any, item_ids: list[str]) -> list[str]:
    return _MaintenanceRows(store).restore(item_ids)


def _hours_since_last_pass(store: Any) -> float:
    try:
        row = store.db.execute(
            "SELECT updated_at FROM items WHERE file_metadata LIKE '%\"consolidated\": true%' "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return 10_000.0
    raw = row["updated_at"] if row else None
    if not raw:
        return 10_000.0
    try:
        elapsed = time.time() - time.mktime(
            time.strptime(str(raw), "%Y-%m-%dT%H:%M:%SZ")
        )
    except (TypeError, ValueError):
        return 10_000.0
    return max(0.0, elapsed / 3600.0)


@dataclass
class _PairSimilarity:
    embed: Any
    vectors: dict[str, list[float]] = field(default_factory=dict)

    def __call__(self, left: str, right: str) -> float:
        from gideon.cognition.knowledge.retrieval import HybridRetriever

        try:
            for text in dict.fromkeys((left, right)):
                if text in self.vectors:
                    continue
                vector = self.embed(text)
                if not vector:
                    break
                self.vectors[text] = vector
            else:
                return HybridRetriever._cosine_similarity(
                    self.vectors[left], self.vectors[right]
                )
        except Exception:
            pass
        return consolidation.token_similarity(left, right)


def _similarity_for(store: Any) -> Any:
    try:
        from gideon.integrations.embedding_providers.registry import get_active_embed_fn

        embed = get_active_embed_fn()
    except Exception:
        return None
    return _PairSimilarity(embed) if embed is not None else None


@dataclass
class _SummaryBatch:
    store: Any
    plan: consolidation.ConsolidationPlan
    context: ActionContext
    written: int = 0
    issues: list[str] = field(default_factory=list)

    async def accept(self, entry: Any) -> None:
        if not isinstance(entry, dict):
            self.issues.append("summary entry is not an object")
            return
        index = _int(entry.get("cluster"), -1)
        if index < 0 or index >= len(self.plan.clusters):
            self.issues.append(f"no cluster at index {index}")
            return
        cluster = self.plan.clusters[index]
        content = str(entry.get("content", "") or "")
        if not content.strip():
            self.issues.append(f"cluster {index}: empty summary, nothing written")
            return
        metadata = consolidation.summary_metadata(cluster, summary_chars=len(content))
        try:
            await _write_summary(entry, cluster, metadata, self.context)
            for item in cluster.items:
                if item.protected:
                    self.issues.append(f"{item.id}: protected, left unarchived")
                else:
                    _archive(self.store, item.id, metadata)
        except Exception as exc:
            logger.warning(
                "consolidation apply failed for cluster %s", index, exc_info=True
            )
            self.issues.append(f"cluster {index}: {exc}")
        else:
            self.written += 1


async def _apply(
    store: Any,
    plan: consolidation.ConsolidationPlan,
    summaries: list[Any],
    ctx: ActionContext,
) -> tuple[int, list[str]]:
    batch = _SummaryBatch(store, plan, ctx)
    for entry in summaries:
        await batch.accept(entry)
    return batch.written, batch.issues


async def _write_summary(
    entry: dict, cluster: consolidation.Cluster, meta: dict, ctx: ActionContext
) -> None:
    from gideon.integrations.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    write = dict(
        kind="insight",
        title=str(entry.get("title", "") or cluster.items[0].title),
        content=str(entry.get("content", "") or ""),
        summary=str(entry.get("summary", "") or ""),
        citations=[f"item:{identifier}" for identifier in cluster.ids],
        lineage=meta,
        tags=["consolidated"],
    )
    receipt = await KnowledgePersistActionProvider().execute(write, ctx)
    if not receipt.success:
        raise RuntimeError(receipt.error or "persist refused the consolidated item")


def _archive(store: Any, item_id: str, meta: dict) -> None:
    row = store.db.execute(
        "SELECT file_metadata FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    metadata = _json(row["file_metadata"]) if row else {}
    metadata.update(
        archived_reason="consolidated",
        summary_of=meta.get("parent_ids", []),
        reflection_count=_int(metadata.get("reflection_count"), 0) + 1,
    )
    store.db.execute(
        "UPDATE items SET is_archived = 1, file_metadata = ? WHERE id = ?",
        (json.dumps(metadata, ensure_ascii=False), item_id),
    )
    store.db.commit()


def _capped(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[:MAX_REPORTED_IDS] if isinstance(value, list) else value
        for key, value in report.items()
    }


def _json(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        value = None
    return value if isinstance(value, dict) else {}


def _truthy(raw: Any) -> bool:
    return (
        raw
        if isinstance(raw, bool)
        else str(raw).strip().lower() in {"true", "1", "yes"}
    )


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


def _config_gate_defaults() -> tuple[int, int]:
    defaults = (consolidation.MIN_HOURS_BETWEEN_PASSES, consolidation.MIN_CLUSTER_SIZE)
    try:
        from gideon.core.config.loader import AppConfig

        settings = AppConfig.load().knowledge
        names = ("consolidate_min_hours", "consolidate_min_cluster")
        hours, size = (
            _int(getattr(settings, name, None), fallback)
            for name, fallback in zip(names, defaults)
        )
        return hours, size
    except Exception:
        logger.debug(
            "knowledge config unreadable; using consolidation constants", exc_info=True
        )
        return defaults


def _open_store() -> Any:
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _pass_context() -> ActionContext:
    return ActionContext(
        event="",
        context="knowledge graph maintenance",
        payload={"source": "graph_maintenance", "pass": "knowledge_consolidation"},
    )


def run_consolidation_pass(*, batch_size: int = 0) -> int:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("consolidation pass skipped: called from a running event loop")
        return 0
    try:
        result = asyncio.run(
            KnowledgeConsolidateActionProvider().execute({}, _pass_context())
        )
    except Exception:
        logger.warning("consolidation pass failed", exc_info=True)
        return 0
    if result.success:
        payload = _json(result.stdout)
        if payload.get("ran"):
            clusters = payload.get("plan", {}).get("clusters")
            return len(clusters) if isinstance(clusters, list) else 0
    else:
        logger.debug("consolidation pass declined: %s", result.error)
    return 0
