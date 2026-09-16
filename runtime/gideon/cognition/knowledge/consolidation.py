"""Pure planning and diagnostics for bounded, provenance-preserving consolidation."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
CLUSTER_SIMILARITY = 0.75
TOKEN_CLUSTER_SIMILARITY = 0.30
PREDEDUP_SIMILARITY = 0.95
MIN_CLUSTER_SIZE = 5
MAX_CLUSTERS_PER_PASS = 10
MAX_ITEMS_PER_PASS = 100
MAX_REFLECTION_COUNT = 3
MIN_HOURS_BETWEEN_PASSES = 6
MIN_NEW_ITEMS = 5
CONSOLIDATION_DOCTRINE = (
    "Consolidate existing data; never generate new knowledge. Preserve EVERY distinct detail "
    "from the inputs — if two inputs disagree, record both with their sources rather than "
    "choosing. Do not add facts, inferences, or context that is not present in the inputs."
)
PROTECTED_ORIGINS = frozenset({"user", "human"})
STUB_BODY_CHARS = 40
_SUBSTANTIVE_RE = re.compile(
    r"(\d)|(/[\w.-]+)|([a-z]+\.[a-z]{2,})|(\b[A-Z][a-z]+[A-Z]\w*)|(`[^`]+`)"
)
_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]")
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


@dataclass
class Item:
    id: str
    kind: str = "fact"
    title: str = ""
    summary: str = ""
    content: str = ""
    logical_key: str = ""
    content_hash: str = ""
    origin: str = ""
    reflection_count: int = 0
    consolidated: bool = False
    is_archived: bool = False
    updated_at: str = ""
    inbound_relations: int = 0
    citations: list[str] = field(default_factory=list)
    chunk_hashes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> Item:
        data = row if isinstance(row, dict) else dict(row)
        metadata = _meta(data.get("file_metadata"))
        fields = {
            name: str(data.get(name, "") or "")
            for name in (
                "id",
                "title",
                "summary",
                "content",
                "logical_key",
                "content_hash",
                "updated_at",
            )
        }
        fields.update(
            kind=str(data.get("kind", "") or "fact"),
            origin=str(metadata.get("origin") or _nested_origin(metadata) or ""),
            reflection_count=_int(metadata.get("reflection_count"), 0),
            consolidated=bool(metadata.get("consolidated", False)),
            is_archived=bool(data.get("is_archived", False)),
            inbound_relations=_int(data.get("inbound_relations"), 0),
            citations=[
                str(value) for value in metadata.get("citations") or () if str(value)
            ],
            chunk_hashes={
                str(key): str(value)
                for key, value in (metadata.get("chunk_hashes") or {}).items()
                if str(key)
            },
        )
        return cls(**fields)

    @property
    def protected(self) -> bool:
        return self.origin.strip().lower() in PROTECTED_ORIGINS

    @property
    def text(self) -> str:
        return "\n".join(map(str, (self.title, self.summary, self.content))).strip()


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    backlog: int = 0

    def __bool__(self) -> bool:
        return self.allowed


def check_gates(
    *,
    unprocessed: int,
    hours_since_last: float,
    lock_held: bool = False,
    min_new_items: int = MIN_NEW_ITEMS,
    min_hours: float = MIN_HOURS_BETWEEN_PASSES,
) -> GateResult:
    reason = ""
    if lock_held:
        reason = "another consolidation pass is running"
    elif unprocessed < max(1, min_new_items):
        reason = f"only {unprocessed} unconsolidated items (floor {min_new_items})"
    elif hours_since_last < min_hours:
        reason = f"last pass {hours_since_last:.1f}h ago (floor {min_hours}h)"
    return GateResult(not bool(reason), reason, unprocessed)


def normalize_for_dedup(text: str) -> str:
    words = _NORMALIZE_RE.sub(" ", (text or "").lower()).split()
    return " ".join(words)


def fuzzy_hash(text: str) -> str:
    normalized = normalize_for_dedup(text)
    digest = hashlib.sha256(normalized.encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


def token_similarity(left: str, right: str) -> float:
    tokens = [
        set(_TOKEN_RE.findall(normalize_for_dedup(text))) for text in (left, right)
    ]
    if all(tokens):
        return len(tokens[0].intersection(tokens[1])) / len(tokens[0].union(tokens[1]))
    return 0.0


@dataclass
class _DuplicateIndex:
    survivors: list[Item] = field(default_factory=list)
    merges: list[tuple[str, str]] = field(default_factory=list)
    hashes: dict[str, Item] = field(default_factory=dict)

    def match(self, item: Item, fingerprint: str) -> Item | None:
        if fingerprint in self.hashes:
            return self.hashes[fingerprint]
        return next(
            (
                candidate
                for candidate in self.survivors
                if token_similarity(candidate.text, item.text) >= PREDEDUP_SIMILARITY
            ),
            None,
        )

    def add(self, item: Item) -> None:
        fingerprint = fuzzy_hash(item.text)
        prior = self.match(item, fingerprint)
        if prior is None:
            self.hashes[fingerprint] = item
            self.survivors.append(item)
            return
        replace = item.protected and not prior.protected
        if replace:
            position = self.survivors.index(prior)
            self.survivors[position] = item
            self.hashes[fingerprint] = item
            self.merges.append((item.id, prior.id))
        else:
            self.merges.append((prior.id, item.id))


def pre_dedup(items: list[Item]) -> tuple[list[Item], list[tuple[str, str]]]:
    index = _DuplicateIndex()
    for item in items:
        index.add(item)
    return index.survivors, index.merges


@dataclass
class Cluster:
    items: list[Item] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [item.id for item in self.items]

    @property
    def size(self) -> int:
        return len(self.items)

    def compression_ratio(self, summary_chars: int) -> float:
        source_chars = sum(len(item.text) for item in self.items)
        return round(summary_chars / source_chars, 4) if source_chars else 0.0


@dataclass
class _ClusterBuilder:
    metric: Any
    threshold: float
    groups: list[list[Item]] = field(default_factory=list)

    def append(self, item: Item) -> None:
        match = next(
            (
                group
                for group in self.groups
                if any(
                    self.metric(member.text, item.text) >= self.threshold
                    for member in group
                )
            ),
            None,
        )
        if match is None:
            self.groups.append([item])
        else:
            match.append(item)

    def finish(self, minimum: int, maximum: int) -> list[Cluster]:
        candidates = (group for group in self.groups if len(group) >= max(2, minimum))
        ordered = sorted(candidates, key=len, reverse=True)
        return [Cluster(group) for group in ordered[:maximum]]


def cluster_items(
    items: list[Item],
    *,
    similarity: Any = None,
    threshold: float | None = None,
    min_size: int = MIN_CLUSTER_SIZE,
    max_clusters: int = MAX_CLUSTERS_PER_PASS,
) -> list[Cluster]:
    default_cut = TOKEN_CLUSTER_SIMILARITY if similarity is None else CLUSTER_SIMILARITY
    builder = _ClusterBuilder(
        similarity or token_similarity, default_cut if threshold is None else threshold
    )
    eligible = [
        item
        for item in items
        if not item.is_archived
        and not item.consolidated
        and item.reflection_count < MAX_REFLECTION_COUNT
    ]
    for item in eligible[:MAX_ITEMS_PER_PASS]:
        builder.append(item)
    return builder.finish(min_size, max_clusters)


@dataclass
class ConsolidationPlan:
    clusters: list[Cluster] = field(default_factory=list)
    pre_dedup_merges: list[tuple[str, str]] = field(default_factory=list)
    skipped_protected: list[str] = field(default_factory=list)
    skipped_lineage: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.clusters or self.pre_dedup_merges)

    def to_dict(self) -> dict[str, Any]:
        cluster_rows = [
            dict(
                ids=cluster.ids,
                size=cluster.size,
                titles=[item.title for item in cluster.items],
            )
            for cluster in self.clusters
        ]
        merge_rows = [
            dict(kept=kept, dropped=dropped) for kept, dropped in self.pre_dedup_merges
        ]
        return dict(
            clusters=cluster_rows,
            pre_dedup_merges=merge_rows,
            skipped_protected=list(self.skipped_protected),
            skipped_lineage=list(self.skipped_lineage),
            items_affected=sum(cluster.size for cluster in self.clusters)
            + len(self.pre_dedup_merges),
        )


def plan_consolidation(items: list[Item], **kwargs: Any) -> ConsolidationPlan:
    plan = ConsolidationPlan()
    candidates = []
    for item in items:
        if item.reflection_count >= MAX_REFLECTION_COUNT:
            plan.skipped_lineage.append(item.id)
        if item.protected:
            plan.skipped_protected.append(item.id)
        else:
            candidates.append(item)
    survivors, plan.pre_dedup_merges = pre_dedup(candidates)
    plan.clusters = cluster_items(survivors, **kwargs)
    return plan


def synthesis_prompt(cluster: Cluster, *, conventions: str = "") -> str:
    header = [CONSOLIDATION_DOCTRINE]
    if conventions.strip():
        header.append(f"Store conventions:\n{conventions.strip()}")
    header.append(f"Consolidate these {cluster.size} related items into ONE item.")
    evidence = [
        "\n".join(
            (
                f"<knowledge_item id={item.id} kind={item.kind}>",
                item.title,
                item.summary,
                item.content,
                "</knowledge_item>",
            )
        )
        for item in cluster.items
    ]
    return "\n\n".join((*header, *evidence))


def summary_metadata(cluster: Cluster, *, summary_chars: int) -> dict[str, Any]:
    depth = max((item.reflection_count for item in cluster.items), default=0)
    return dict(
        parent_ids=cluster.ids,
        reflection_count=depth + 1,
        consolidated=True,
        source_count=cluster.size,
        compression_ratio=cluster.compression_ratio(summary_chars),
    )


@dataclass
class HealthReport:
    stubs: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    broken_citations: list[dict[str, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    unindexed: list[str] = field(default_factory=list)
    stale_chunks: list[dict[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not any(getattr(self, category) for category in _HEALTH_CATEGORIES)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            category: list(getattr(self, category)) for category in _HEALTH_CATEGORIES
        }
        payload["clean"] = self.clean
        return payload


_HEALTH_CATEGORIES = (
    "stubs",
    "orphans",
    "broken_citations",
    "expired",
    "unindexed",
    "stale_chunks",
)


@dataclass
class _HealthScan:
    known: set[str]
    indexed: set[str] | None
    report: HealthReport = field(default_factory=HealthReport)

    def inspect(self, item: Item) -> None:
        if item.is_archived:
            return
        checks = (
            ("stubs", _is_stub(item)),
            (
                "orphans",
                item.inbound_relations == 0 and item.kind not in ("overview", "probe"),
            ),
            ("unindexed", self.indexed is not None and item.id not in self.indexed),
        )
        for category, found in checks:
            if found:
                getattr(self.report, category).append(item.id)
        self.report.broken_citations.extend(
            dict(item_id=item.id, citation=citation)
            for citation in item.citations
            if citation.startswith("item:") and citation[5:] not in self.known
        )


def check_health(
    items: list[Item],
    *,
    known_ids: set[str] | None = None,
    indexed_ids: set[str] | None = None,
    expired_ids: set[str] | None = None,
) -> HealthReport:
    scan = _HealthScan(
        {item.id for item in items} if known_ids is None else known_ids, indexed_ids
    )
    for item in items:
        scan.inspect(item)
    if expired_ids:
        scan.report.expired = sorted(expired_ids)
    return scan.report


def _is_stub(item: Item) -> bool:
    body = " ".join(map(str, (item.content, item.summary))).strip()
    substantial = len(body) >= STUB_BODY_CHARS or bool(_SUBSTANTIVE_RE.search(body))
    return not substantial


def changed_sections(stored: dict[str, str], fresh: dict[str, str]) -> list[str]:
    changed = []
    for name in sorted(fresh):
        previous, current = stored.get(name), fresh[name]
        if (
            previous is not None
            and _same_hash_form(previous, current)
            and previous == current
        ):
            continue
        changed.append(name)
    return changed


def _same_hash_form(left: str, right: str) -> bool:
    return len(left) == len(right)


def chunk_hashes(sections: dict[str, str]) -> dict[str, str]:
    return dict((str(name), fuzzy_hash(str(body))) for name, body in sections.items())


@dataclass(frozen=True)
class _GapIndex:
    names: set[str]
    floor: int

    def candidate(self, entity: str, references: list[str]) -> dict[str, Any] | None:
        slug = _slug(entity)
        if not slug or any(slug in name for name in self.names):
            return None
        identifiers = sorted(set(references))
        if len(identifiers) >= self.floor:
            return dict(
                entity=entity,
                slug=slug,
                referrers=identifiers,
                mentions=len(identifiers),
            )
        return None


def phantom_hubs(
    items: list[Item], *, mentions: dict[str, list[str]], min_mentions: int = 3
) -> list[dict[str, Any]]:
    existing = {
        value
        for item in items
        for value in (
            item.logical_key if item.logical_key else None,
            _slug(item.title) if item.title else None,
        )
        if value is not None
    }
    index = _GapIndex(existing, max(2, min_mentions))
    candidates = []
    for entity, references in mentions.items():
        candidate = index.candidate(entity, references)
        if candidate is not None:
            candidates.append(candidate)
    return sorted(
        candidates, key=lambda entry: (-int(entry["mentions"]), str(entry["entity"]))
    )


def _slug(text: str) -> str:
    segments = re.split(r"[^a-z0-9]+", (text or "").lower())
    return "-".join(segments).strip("-")


def lint_due(
    *, persists_since_last: int, every_n: int, health_clean: bool
) -> tuple[bool, str]:
    if health_clean:
        minimum = max(1, every_n)
        reached = persists_since_last >= minimum
        return reached, (
            ""
            if reached
            else f"{persists_since_last} writes since last lint (cadence {minimum})"
        )
    return (
        False,
        "health findings outstanding — fix those first (linting a stub wastes tokens)",
    )


def _meta(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = None
    return raw if isinstance(raw, dict) else {}


def _nested_origin(meta: dict[str, Any]) -> str:
    source = meta.get("source")
    return str(source.get("origin", "") or "") if isinstance(source, dict) else ""


def _int(raw: Any, fallback: int) -> int:
    numeric = isinstance(raw, (int, float)) and not isinstance(raw, bool)
    return int(raw) if numeric else fallback
