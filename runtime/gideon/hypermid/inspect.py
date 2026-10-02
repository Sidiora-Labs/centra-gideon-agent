from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Iterable, Mapping

from gideon.cognition.context_headroom import Component, Headroom
from gideon.hypermid.cache import CacheGeneration
from gideon.hypermid.models import Cursor, Scope


@dataclass(frozen=True, slots=True)
class WriterStatus:
    module_id: str
    epoch: int
    generation: int

    def to_dict(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "epoch": self.epoch,
            "generation": self.generation,
        }


def _component_evidence(components: Iterable[Component]) -> tuple[list[dict], list[str]]:
    evidence: list[dict] = []
    mismatches: list[str] = []
    for component in components:
        item = component.inspection()
        actual = hashlib.sha256(component.text.encode("utf-8")).hexdigest()
        if not component.content_digest:
            mismatches.append(f"{component.name}:missing")
        elif component.content_digest != actual:
            mismatches.append(f"{component.name}:content_digest")
        if not component.covered_digest:
            mismatches.append(f"{component.name}:covered_digest")
        evidence.append(item)
    return evidence, mismatches


def _cache_evidence(cache: CacheGeneration | None) -> dict[str, object]:
    if cache is None:
        return {"freshness": "unavailable", "generation": None, "bytes": 0, "regions": []}
    regions = []
    total = 0
    for name, region in (
        ("baseline", cache.baseline),
        ("delta", cache.delta),
        ("tail", cache.live_tail),
    ):
        size = len(region.bytes)
        total += size
        regions.append(
            {
                "name": name,
                "digest": region.digest,
                "bytes": size,
                "token_mass": region.token_mass,
                "item_count": len(region.item_ids),
                "summary_count": len(region.summary_ids),
            }
        )
    return {
        "freshness": "fresh" if cache.boundary_reason is None else "boundary",
        "generation": cache.generation,
        "policy_revision": cache.policy_revision,
        "provider_profile_digest": cache.provider_profile_digest,
        "bytes": total,
        "boundary_reason": cache.boundary_reason,
        "regions": regions,
    }


def _projection_region_evidence(
    name: str, region: Mapping[str, object], blocks: list[object]
) -> tuple[dict[str, object], list[str]]:
    raw_item_ids = region.get("item_ids")
    raw_summary_ids = region.get("summary_ids")
    item_values = raw_item_ids if isinstance(raw_item_ids, list) else []
    summary_values = raw_summary_ids if isinstance(raw_summary_ids, list) else []
    item_ids = {value for value in item_values if isinstance(value, str)}
    summary_ids = {value for value in summary_values if isinstance(value, str)}
    selected: list[object] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        source_ids = block.get("source_item_ids", ())
        if isinstance(source_ids, list) and any(value in item_ids for value in source_ids):
            selected.append(block)
            continue
        block_id = block.get("block_id")
        if isinstance(block_id, str) and any(
            block_id.startswith(f"summary:{summary_id}:")
            for summary_id in summary_ids
        ):
            selected.append(block)
    encoded = json.dumps(
        selected,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    for block in selected:
        block_bytes = json.dumps(
            block,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(block_bytes).to_bytes(8, "big"))
        digest.update(block_bytes)
    mismatches = []
    if (
        len(item_ids) != len(item_values)
        or len(summary_ids) != len(summary_values)
        or type(region.get("token_mass")) is not int
        or region.get("token_mass", -1) < 0
    ):
        mismatches.append(f"projection:{name}_metadata")
    if region.get("digest") != digest.hexdigest():
        mismatches.append(f"projection:{name}_digest")
    if len(selected) != len(item_ids) + len(summary_ids):
        mismatches.append(f"projection:{name}_coverage")
    return (
        {
            "name": name,
            "digest": region.get("digest"),
            "bytes": len(encoded),
            "token_mass": region.get("token_mass"),
            "item_count": len(item_ids),
            "summary_count": len(summary_ids),
        },
        mismatches,
    )


def inspect_runtime(
    *,
    scope: Scope,
    writer: str,
    writer_status: WriterStatus,
    cursor: Cursor | None = None,
    components: Iterable[Component] = (),
    headroom: Headroom | None = None,
    cache: CacheGeneration | None = None,
    recall: object | None = None,
    observed_at_ms: int | None = None,
) -> dict[str, object]:
    if writer not in {"gideon", "hypermid"}:
        raise ValueError("writer must be gideon or hypermid")
    component_values = tuple(components)
    component_evidence, mismatches = _component_evidence(component_values)
    digest_payload = json.dumps(
        component_evidence, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    active_digest = hashlib.sha256(digest_payload).hexdigest()
    has_evidence = bool(component_evidence or headroom or cache or recall)
    state = "complete" if has_evidence and not mismatches else "unavailable"
    recovery_action = None
    if mismatches:
        state = "unreadable"
        recovery_action = "rebuild context projection from authoritative sources"
    stale_policy = bool(
        cache is not None
        and any(
            component.policy_revision != cache.policy_revision
            for component in component_values
        )
    )
    if state == "complete" and stale_policy:
        state = "stale"
        recovery_action = "advance the cache generation for the active policy revision"
    recall_evidence = None
    if isinstance(recall, Mapping):
        recall_evidence = dict(recall)
    elif recall is not None and callable(getattr(recall, "inspection", None)):
        recall_evidence = recall.inspection()
    provenance = sorted(
        {
            (
                str(component.source),
                int(component.policy_revision),
                bool(component.content_digest and component.covered_digest),
            )
            for component in component_values
        }
    )
    result: dict[str, object] = {
        "state": state,
        "writer": writer,
        "writer_status": writer_status.to_dict(),
        "scope": scope.to_wire(),
        "observed_at": observed_at_ms if observed_at_ms is not None else int(time.time() * 1000),
        "digest_health": {
            "state": (
                "mismatch" if mismatches else ("stale" if stale_policy else "healthy")
            ),
            "component_count": len(component_evidence),
            "mismatches": mismatches,
        },
        "active_digest": active_digest,
        "cache": _cache_evidence(cache),
        "recall_arms": recall_evidence["arms"] if recall_evidence else [],
        "recall": recall_evidence,
        "provenance": [
            {"source": source, "revision": revision, "verified": verified}
            for source, revision, verified in provenance
        ],
        "components": component_evidence,
        "headroom": headroom.to_dict() if headroom is not None else None,
    }
    if cursor is not None:
        result["cursor"] = cursor.to_wire()
    if recovery_action is not None:
        result["recovery_action"] = recovery_action
    return result


def inspect_primary_context(
    assembled: object,
    *,
    scope: Scope,
    writer_status: WriterStatus,
    observed_at_ms: int | None = None,
) -> dict[str, object]:
    metadata = getattr(assembled, "metadata", None)
    hypermid = metadata.get("hypermid") if isinstance(metadata, Mapping) else None
    if not isinstance(hypermid, Mapping):
        return {
            "state": "unavailable",
            "writer": "gideon",
            "writer_status": writer_status.to_dict(),
            "scope": scope.to_wire(),
            "observed_at": (
                observed_at_ms if observed_at_ms is not None else int(time.time() * 1000)
            ),
            "digest_health": {"state": "unavailable", "component_count": 0, "mismatches": []},
            "cache": {"freshness": "unavailable", "generation": None, "bytes": 0, "regions": []},
            "recall_arms": [],
            "provenance": [],
            "recovery_action": "enable a healthy primary Hypermid context engine",
        }
    projection = hypermid.get("projection")
    model_budget = hypermid.get("model_budget")
    blocks = projection.get("blocks") if isinstance(projection, Mapping) else None
    mismatches: list[str] = []
    if not isinstance(blocks, list):
        mismatches.append("projection:blocks")
        blocks = []
    encoded_blocks = json.dumps(
        blocks,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    projection_digest = hashlib.sha256(encoded_blocks).hexdigest()
    if not isinstance(projection, Mapping) or projection.get("output_digest") != projection_digest:
        mismatches.append("projection:output_digest")
    if isinstance(projection, Mapping) and projection.get("scope") != scope.to_wire():
        mismatches.append("projection:scope")
    serialized_input = {
        "blocks": blocks,
        "model_budget": model_budget,
        "provider_profile_digest": (
            projection.get("provider_profile_digest")
            if isinstance(projection, Mapping)
            else None
        ),
        "render_mode": projection.get("render_mode") if isinstance(projection, Mapping) else None,
    }
    serialized_digest = hashlib.sha256(
        json.dumps(
            serialized_input,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if hypermid.get("serialized_digest") != serialized_digest:
        mismatches.append("projection:serialized_digest")
    components = tuple(getattr(assembled, "components", ()) or ())
    component_evidence, component_mismatches = _component_evidence(components)
    mismatches.extend(component_mismatches)
    assembled_tokens = sum(int(item["tokens"]) for item in component_evidence)
    recall_tokens = sum(
        int(item["tokens"])
        for item in component_evidence
        if item.get("source") in {"hypermid.memory.daemon", "gideon.memory.fallback"}
    )
    projected_tokens = None
    max_input_tokens = None
    if isinstance(model_budget, Mapping):
        token_fields = (
            model_budget.get("baseline_tokens"),
            model_budget.get("delta_tokens"),
            model_budget.get("tail_tokens"),
        )
        if all(type(value) is int and value >= 0 for value in token_fields):
            projected_tokens = sum(int(value) for value in token_fields)
        candidate_max = model_budget.get("max_input_tokens")
        if type(candidate_max) is int and candidate_max >= 0:
            max_input_tokens = candidate_max
    if projected_tokens is None or max_input_tokens is None:
        mismatches.append("budget:model")
    elif assembled_tokens > max_input_tokens:
        mismatches.append("budget:assembled_overflow")
    assembled_message = getattr(assembled, "message", None)
    if not isinstance(assembled_message, str):
        mismatches.append("components:message_missing")
        assembled_message = ""
    elif "".join(component.text for component in components) != assembled_message:
        mismatches.append("components:assembly_digest")
    active_digest = hashlib.sha256(assembled_message.encode("utf-8")).hexdigest()
    cache_outcome = hypermid.get("cache")
    applied = isinstance(cache_outcome, Mapping) and cache_outcome.get("kind") == "applied"
    regions = []
    if isinstance(projection, Mapping):
        for name in ("baseline", "delta", "tail"):
            region = projection.get(name)
            if isinstance(region, Mapping):
                evidence, region_mismatches = _projection_region_evidence(
                    name, region, blocks
                )
                regions.append(evidence)
                mismatches.extend(region_mismatches)
            else:
                mismatches.append(f"projection:{name}_region")
    projection_generation = (
        projection.get("generation") if isinstance(projection, Mapping) else None
    )
    cache_generation_matches = (
        isinstance(cache_outcome, Mapping)
        and cache_outcome.get("generation") == projection_generation
    )
    if not cache_generation_matches:
        mismatches.append("cache:generation")
    if writer_status.generation != projection_generation:
        mismatches.append("writer:generation")
    if hypermid.get("writer_fence_epoch") != writer_status.epoch:
        mismatches.append("writer:fence_epoch")
    recall = hypermid.get("recall")
    recall_evidence = dict(recall) if isinstance(recall, Mapping) else None
    cursor = hypermid.get("cursor")
    recall_state = recall_evidence.get("state") if recall_evidence else None
    recall_ready = recall_state in {"daemon", "fallback"}
    summary_count = sum(int(region["summary_count"]) for region in regions)
    summary = hypermid.get("summary")
    summary_evidence = dict(summary) if isinstance(summary, Mapping) else None
    if summary_evidence is None:
        mismatches.append("summary:evidence")
    else:
        authorized = summary_evidence.get("authorized_records")
        selected = summary_evidence.get("selected_records")
        memory_cursor = summary_evidence.get("memory_cursor")
        cursor_valid = memory_cursor is None
        if isinstance(memory_cursor, Mapping):
            try:
                Cursor.from_wire(memory_cursor)
            except (TypeError, ValueError):
                pass
            else:
                cursor_valid = True
        if (
            type(authorized) is not int
            or authorized < 0
            or type(selected) is not int
            or selected < 0
            or selected > authorized
            or selected != summary_count
            or not cursor_valid
        ):
            mismatches.append("summary:evidence")
    state = (
        "unreadable"
        if mismatches
        else ("complete" if applied and recall_ready else "stale")
    )
    provenance = [
        {
            "source": "hypermid.context",
            "revision": (
                projection.get("policy_revision")
                if isinstance(projection, Mapping)
                else None
            ),
            "verified": not mismatches,
        }
    ]
    if summary_evidence is not None:
        provenance.append(
            {
                "source": "hypermid.summary",
                "revision": (
                    projection.get("policy_revision")
                    if isinstance(projection, Mapping)
                    else None
                ),
                "verified": "summary:evidence" not in mismatches,
                "authorized_count": summary_evidence.get("authorized_records"),
                "selected_count": summary_count,
            }
        )
    if recall_evidence is not None:
        provenance.append(
            {
                "source": recall_evidence.get("source", "memory"),
                "revision": (
                    projection.get("policy_revision")
                    if isinstance(projection, Mapping)
                    else None
                ),
                "verified": recall_ready,
            }
        )
    result: dict[str, object] = {
        "state": state,
        "writer": "hypermid",
        "writer_status": writer_status.to_dict(),
        "scope": scope.to_wire(),
        "observed_at": (
            observed_at_ms if observed_at_ms is not None else int(time.time() * 1000)
        ),
        "digest_health": {
            "state": "mismatch" if mismatches else "healthy",
            "component_count": len(component_evidence),
            "mismatches": mismatches,
            "projection_digest": projection_digest,
            "serialized_digest": serialized_digest,
        },
        "active_digest": active_digest,
        "cache": {
            "freshness": "fresh" if applied and cache_generation_matches else "stale",
            "generation": (
                cache_outcome.get("generation") if isinstance(cache_outcome, Mapping) else None
            ),
            "bytes": sum(int(region["bytes"]) for region in regions),
            "bytes_known": len(regions) == 3,
            "reason": (
                cache_outcome.get("reason_code")
                if isinstance(cache_outcome, Mapping)
                else "cache_outcome_missing"
            ),
            "regions": regions,
        },
        "recall_arms": recall_evidence.get("arms", []) if recall_evidence else [],
        "recall": recall_evidence,
        "summary": summary_evidence,
        "provenance": provenance,
        "components": component_evidence,
        "model_budget": model_budget if isinstance(model_budget, Mapping) else None,
        "budget_evidence": {
            "projected_tokens": projected_tokens,
            "assembled_tokens": assembled_tokens,
            "recall_tokens": recall_tokens,
            "max_input_tokens": max_input_tokens,
            "within_limit": (
                max_input_tokens is not None and assembled_tokens <= max_input_tokens
            ),
        },
    }
    if isinstance(cursor, Mapping):
        result["cursor"] = dict(cursor)
    if state != "complete":
        result["recovery_action"] = (
            "rebuild primary projection from authoritative sources"
            if mismatches
            else (
                "restore scoped memory authority or its local fallback"
                if not recall_ready
                else "advance the primary cache generation"
            )
        )
    return result
