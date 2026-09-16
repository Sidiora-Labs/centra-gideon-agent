"""Resolve extracted facts against existing memory before applying store mutations."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from gideon.cognition import memory_holder

logger = logging.getLogger(__name__)

VERDICT_ADD = "ADD"
VERDICT_UPDATE = "UPDATE"
VERDICT_SUPERSEDE = "SUPERSEDE"
VERDICT_NOOP = "NOOP"
VERDICTS = (VERDICT_ADD, VERDICT_UPDATE, VERDICT_SUPERSEDE, VERDICT_NOOP)
CONFLICT_PROVENANCE = "conflict"
CONFLICT_EVENT = "conflict_keep_both"
MAX_OVERLAPS_PER_CANDIDATE = 4
OVERLAP_RATIO = 0.5
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "in",
        "for",
        "and",
        "or",
        "not",
        "is",
        "of",
        "on",
        "with",
        "at",
    }
)


def _words(text: str) -> set[str]:
    tokens = set(re.findall(r"\w+", (text or "").lower()))
    return {token for token in tokens.difference(_STOPWORDS) if len(token) > 2}


def _namespace(key: str) -> str:
    parent, _, _leaf = key.rpartition(".")
    return parent if "." in parent else ""


def _value_str(value: object) -> str:
    if not isinstance(value, str):
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)
    return value


def _attributed_payload(payload: dict, holder: str, weight: float) -> dict:
    if holder:
        payload.update(holder=holder, weight=round(weight, 2))
    return payload


@dataclass(frozen=True)
class Overlap:
    key: str
    value_str: str
    holder: str = ""
    weight: float = 1.0
    why: str = "keyword"

    def as_payload(self) -> dict:
        return _attributed_payload(
            dict(key=self.key, value=self.value_str, matched_by=self.why),
            self.holder,
            self.weight,
        )


@dataclass
class Candidate:
    index: int
    key: str
    value: object
    confidence: float = 0.5
    holder: str = ""
    weight: float = 1.0
    delete: bool = False
    overlaps: list[Overlap] = field(default_factory=list)

    @property
    def value_str(self) -> str:
        return _value_str(self.value)

    def as_payload(self) -> dict:
        payload = dict(index=self.index, key=self.key, value=self.value_str)
        payload["existing"] = [entry.as_payload() for entry in self.overlaps]
        return _attributed_payload(payload, self.holder, self.weight)


@dataclass
class Decision:
    index: int
    verdict: str = VERDICT_ADD
    target: str = ""
    reason: str = ""
    unsure: bool = False


@dataclass
class FormationReport:
    added: int = 0
    updated: int = 0
    superseded: int = 0
    noop: int = 0
    rejected: int = 0
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    degraded: bool = False

    def summary(self) -> str:
        labels = [
            f"{name}={getattr(self, name)}"
            for name in ("added", "updated", "superseded", "noop")
        ]
        optional = (
            (self.rejected, f"rejected={self.rejected}"),
            (self.conflicts, f"conflicts={len(self.conflicts)}"),
            (self.degraded, "decide=degraded"),
        )
        labels.extend(label for enabled, label in optional if enabled)
        return " ".join(labels)


def candidates_from_extract(
    items: Iterable[object], *, holder_attribution: bool, limit: int
) -> list[Candidate]:
    selected = (
        row for row in list(items)[:limit] if isinstance(row, dict) and "key" in row
    )
    candidates = []
    for index, row in enumerate(selected):
        key = str(row["key"])
        try:
            confidence = float(row.get("confidence", 0.5))
        except (ValueError, TypeError):
            confidence = 0.5
        attribution = (
            memory_holder.normalize_holder(row.get("holder"))
            if holder_attribution
            else ""
        )
        candidates.append(
            Candidate(
                index=index,
                key=key,
                value=row.get("value"),
                confidence=confidence,
                holder=attribution,
                weight=memory_holder.normalize_weight(
                    attribution, row.get("weight", 1.0)
                ),
                delete=bool(row.get("delete")),
            )
        )
    return candidates


class _CollisionCorpus:
    """Index the snapshot once; retain arm precedence when projecting each candidate."""

    def __init__(self, store):
        self.entries: dict[str, Overlap] = {}
        self.namespaces: dict[str, list[str]] = defaultdict(list)
        self.terms: dict[str, set[str]] = {}
        self.postings: dict[str, set[str]] = defaultdict(set)
        rows = store.db.execute(
            "SELECT key, value_json, holder, weight FROM semantic_memory WHERE is_deleted = 0 ORDER BY key"
        ).fetchall()
        for row in rows:
            try:
                value = json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                value = row["value_json"]
            key = row["key"]
            self.entries[key] = Overlap(
                key,
                _value_str(value),
                memory_holder.normalize_holder(row["holder"]),
                float(1.0 if row["weight"] is None else row["weight"]),
            )
        for key in sorted(self.entries):
            self.namespaces[_namespace(key)].append(key)

    @staticmethod
    def terms_for(key: str, value: str) -> set[str]:
        return _words(f"{key.replace('.', ' ').replace('_', ' ')} {value}")

    def keyword_matches(self, words: set[str]) -> list[str]:
        if not self.terms:
            for key, entry in self.entries.items():
                tokens = self.terms_for(key, entry.value_str)
                self.terms[key] = tokens
                for token in tokens:
                    self.postings[token].add(key)
        potential = set().union(*(self.postings[token] for token in words))
        return [
            key
            for key in sorted(potential)
            if len(words & self.terms[key]) / min(len(words), len(self.terms[key]))
            >= OVERLAP_RATIO
        ]

    def overlaps_for(self, store, candidate: Candidate) -> list[Overlap]:
        selected: dict[str, Overlap] = {}

        def admit(keys, reason):
            for key in keys:
                if key in self.entries and key not in selected:
                    selected[key] = replace(self.entries[key], why=reason)

        admit((candidate.key,), "same_key")
        parent = _namespace(candidate.key)
        if parent:
            admit(self.namespaces[parent], "key_namespace")
        words = self.terms_for(candidate.key, candidate.value_str)
        if words:
            admit(self.keyword_matches(words), "keyword")
        try:
            scores = store._graph_boosts(candidate.value_str)
        except Exception:
            scores = {}
        admit(sorted(scores, key=lambda key: (-scores[key], key)), "graph")
        return list(selected.values())[:MAX_OVERLAPS_PER_CANDIDATE]


def gather(vs, candidates: Sequence[Candidate]) -> list[Candidate]:
    corpus = _CollisionCorpus(vs)
    for candidate in candidates:
        candidate.overlaps = corpus.overlaps_for(vs, candidate)
    return list(candidates)


def build_decide_prompt(candidates: Sequence[Candidate]) -> str:
    selected = tuple(candidate for candidate in candidates if candidate.overlaps)
    if selected:
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        template = render_snippet_block("memory-decide")
        if template.strip():
            records = [candidate.as_payload() for candidate in selected]
            return "{}\n\n## Candidates\n{}\n".format(
                template, json.dumps(records, indent=1)
            )
    return ""


def _decoded_verdicts(rows, admitted: set[int]):
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("index", -1))
        except (ValueError, TypeError):
            continue
        if number not in admitted:
            continue
        action = str(row.get("verdict", "")).strip().upper()
        if action in VERDICTS:
            yield Decision(
                number,
                action,
                str(row.get("target", "") or ""),
                str(row.get("reason", "") or "")[:200],
                bool(row.get("unsure")),
            )


def parse_decisions(
    result: object, candidates: Sequence[Candidate]
) -> dict[int, Decision]:
    if not isinstance(result, dict) or not isinstance(result.get("verdicts"), list):
        return {}
    admitted = {candidate.index for candidate in candidates}
    return {
        decision.index: decision
        for decision in _decoded_verdicts(result["verdicts"], admitted)
    }


class _VerdictPolicy:
    def __init__(self, candidate: Candidate):
        self.candidate = candidate
        self.targets = {entry.key: entry for entry in candidate.overlaps}

    def fallback(self, reason: str) -> Decision:
        return Decision(index=self.candidate.index, reason=reason)

    def resolve(self, requested: Decision | None) -> Decision:
        if requested is None:
            return self.fallback("no verdict — default add")
        if requested.verdict not in (VERDICT_UPDATE, VERDICT_SUPERSEDE):
            return replace(requested, index=self.candidate.index, unsure=False)
        destination = requested.target
        if not destination:
            incumbent = next(
                (entry for entry in self.candidate.overlaps if entry.why == "same_key"),
                None,
            )
            if incumbent is None:
                return self.fallback("update/supersede without a target row")
            destination = incumbent.key
        if destination not in self.targets:
            return self.fallback(f"unknown target {destination!r}")
        action = requested.verdict
        if action == VERDICT_SUPERSEDE and destination == self.candidate.key:
            action = VERDICT_UPDATE
        outranked = memory_holder.precedence(
            self.candidate.holder
        ) < memory_holder.precedence(self.targets[destination].holder)
        return Decision(
            self.candidate.index,
            action,
            destination,
            requested.reason,
            True if outranked else requested.unsure,
        )


def adjudicate(cand: Candidate, decision: Decision | None) -> Decision:
    return _VerdictPolicy(cand).resolve(decision)


def _write(vs, cand: Candidate, source: str, *, holder_attribution: bool) -> bool:
    attribution = (
        dict(holder=cand.holder, weight=cand.weight)
        if holder_attribution and cand.holder
        else {}
    )
    origin = "user_explicit" if cand.confidence >= 1.0 else source
    return (
        vs.set_semantic(cand.key, cand.value, cand.confidence, origin, **attribution)
        is None
    )


class _ConflictJournal:
    def __init__(self, store):
        self.store = store

    def record(self, new_key: str, old_key: str, source: str, reason: str) -> None:
        for surface in ("WAL event", "edge"):
            try:
                if surface == "WAL event":
                    self.store.append_event(
                        event_type=CONFLICT_EVENT,
                        memory_type="semantic",
                        memory_key=new_key,
                        old_value=old_key,
                        new_value=reason[:200] or None,
                        source=source,
                    )
                else:
                    self.store.graph.add_link(
                        from_kind="semantic",
                        from_ref=new_key,
                        to_ref=old_key,
                        link_type="references",
                        provenance=CONFLICT_PROVENANCE,
                        context=reason[:200] or None,
                        source=source,
                    )
            except Exception:
                logger.debug(
                    "conflict %s failed for %s/%s",
                    surface,
                    new_key,
                    old_key,
                    exc_info=True,
                )

    def live(self) -> list[dict]:
        try:
            links = self.store.db.execute(
                "SELECT from_ref, to_ref, context, created_at FROM mem_links "
                "WHERE link_type = 'references' AND provenance = ? ORDER BY id DESC",
                (CONFLICT_PROVENANCE,),
            ).fetchall()
        except Exception:
            logger.debug("conflict scan unavailable", exc_info=True)
            return []
        result = []
        for link in links:
            active = self.store.db.execute(
                "SELECT key FROM semantic_memory WHERE key IN (?, ?) AND is_deleted = 0",
                (link["from_ref"], link["to_ref"]),
            ).fetchall()
            if len(active) == 2:
                result.append(
                    dict(
                        new_key=link["from_ref"],
                        old_key=link["to_ref"],
                        reason=link["context"] or "",
                        created_at=link["created_at"],
                    )
                )
        return result


def flag_conflict(
    vs, new_key: str, old_key: str, *, source: str, reason: str = ""
) -> None:
    _ConflictJournal(vs).record(new_key, old_key, source, reason)


def conflicts(vs) -> list[dict]:
    return _ConflictJournal(vs).live()


@dataclass(frozen=True)
class _FormationMutation:
    candidate: Candidate
    counter: str = "added"
    conflict_with: str | None = None
    retire: str | None = None

    @classmethod
    def for_verdict(cls, candidate: Candidate, decision: Decision):
        if decision.verdict == VERDICT_UPDATE:
            if decision.unsure and decision.target != candidate.key:
                return cls(candidate, conflict_with=decision.target)
            destination = (
                candidate
                if decision.target == candidate.key
                else _retarget(candidate, decision.target)
            )
            return cls(destination, counter="updated")
        if decision.verdict == VERDICT_SUPERSEDE:
            if decision.unsure:
                return cls(candidate, conflict_with=decision.target)
            return cls(candidate, retire=decision.target)
        return cls(candidate)


class _FormationWriter:
    def __init__(self, store, source: str, holder_attribution: bool):
        self.store, self.source, self.holder_attribution = (
            store,
            source,
            holder_attribution,
        )
        self.report = FormationReport()

    def execute(self, candidate: Candidate, decision: Decision | None) -> None:
        verdict = adjudicate(candidate, decision)
        if candidate.delete:
            self.report.superseded += bool(
                self.store.delete_semantic(candidate.key, self.source)
            )
            return
        if verdict.verdict == VERDICT_NOOP:
            self.report.noop += 1
            return
        mutation = _FormationMutation.for_verdict(candidate, verdict)
        if not _write(
            self.store,
            mutation.candidate,
            self.source,
            holder_attribution=self.holder_attribution,
        ):
            self.report.rejected += 1
            return
        setattr(
            self.report, mutation.counter, getattr(self.report, mutation.counter) + 1
        )
        if mutation.conflict_with is not None:
            flag_conflict(
                self.store,
                candidate.key,
                mutation.conflict_with,
                source=self.source,
                reason=verdict.reason,
            )
            self.report.conflicts.append((candidate.key, mutation.conflict_with))
        if mutation.retire is not None:
            self.report.superseded += bool(
                self.store.supersede_semantic(
                    mutation.retire, candidate.key, self.source
                )
            )


def apply_decisions(
    vs,
    candidates: Sequence[Candidate],
    decisions: dict[int, Decision],
    *,
    source: str,
    holder_attribution: bool = False,
) -> FormationReport:
    writer = _FormationWriter(vs, source, holder_attribution)
    for candidate in candidates:
        writer.execute(candidate, decisions.get(candidate.index))
    return writer.report


def _retarget(cand: Candidate, key: str) -> Candidate:
    return replace(cand, key=key, delete=False)
