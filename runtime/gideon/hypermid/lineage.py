from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping

from .records import (
    LineageEdge,
    LineageRelation,
    MemoryContractError,
    ProvenanceSpan,
    SourceSnapshot,
)


def require_acyclic_lineage(
    child_id: str,
    proposed: Iterable[LineageEdge],
    existing_parents: Mapping[str, Iterable[LineageEdge]],
) -> None:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for node, edges in existing_parents.items():
        for edge in edges:
            if edge.relation.acyclic:
                adjacency[node].add(edge.parent_id)
    for edge in proposed:
        if edge.relation.acyclic:
            adjacency[child_id].add(edge.parent_id)

    pending = list(adjacency[child_id])
    visited: set[str] = set()
    while pending:
        candidate = pending.pop()
        if candidate == child_id:
            raise MemoryContractError("LINEAGE_CYCLE", "lineage would create a cycle")
        if candidate in visited:
            continue
        visited.add(candidate)
        pending.extend(adjacency.get(candidate, ()))


def dependent_descendants(
    source_ids: Iterable[str],
    edges: Iterable[tuple[str, str, LineageRelation]],
) -> tuple[str, ...]:
    children: dict[str, set[str]] = defaultdict(set)
    for child_id, parent_id, relation in edges:
        if relation in {
            LineageRelation.DERIVED_FROM,
            LineageRelation.MERGED_FROM,
            LineageRelation.SPLIT_FROM,
        }:
            children[parent_id].add(child_id)

    queued = deque(sorted(set(source_ids)))
    descendants: set[str] = set()
    while queued:
        parent = queued.popleft()
        for child in sorted(children.get(parent, ())):
            if child not in descendants:
                descendants.add(child)
                queued.append(child)
    return tuple(sorted(descendants))


def recover_provenance(
    spans: Iterable[ProvenanceSpan], sources: Mapping[str, SourceSnapshot]
) -> tuple[str, ...]:
    recovered: list[str] = []
    for span in spans:
        source = sources.get(span.source_id)
        if source is None:
            raise MemoryContractError(
                "SOURCE_NOT_FOUND", f"provenance source {span.source_id} is unavailable"
            )
        recovered.append(span.recover(source))
    return tuple(recovered)
