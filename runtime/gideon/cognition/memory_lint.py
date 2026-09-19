"""Run deterministic memory health checks and retain optional diagnostic results."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
_STALE_DAYS = 90
_SUPERSEDED_RETENTION_DAYS = 90
_NEAR_DUP_RATIO = 0.7


@dataclass
class LintReport:
    auto_fixed: dict[str, int] = field(default_factory=dict)
    flags: list[dict] = field(default_factory=list)

    def add_flag(self, check: str, key: str, detail: str) -> None:
        self.flags += [dict(check=check, key=key, detail=detail)]

    def to_dict(self) -> dict:
        return dict(
            auto_fixed=self.auto_fixed, flags=self.flags, flag_count=len(self.flags)
        )


def _parse_iso(s: str | None) -> datetime | None:
    if s:
        try:
            parsed = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _keywords(text: str) -> set[str]:
    ignored = {
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
    }
    tokens = set(re.findall(r"\w+", text.lower())).difference(ignored)
    return {token for token in tokens if len(token) > 2}


class _MemorySweep:
    def __init__(self, store, now: datetime, judge):
        self.store, self.now, self.judge = store, now, judge
        self.report = LintReport()
        self.facts: list[dict] = []
        self.related: set[tuple[str, str]] = set()

    def expire_superseded(self) -> None:
        count = 0
        deadline = (self.now - timedelta(days=_SUPERSEDED_RETENTION_DAYS)).isoformat()
        try:
            expired = self.store.db.execute(
                "SELECT key FROM semantic_memory WHERE superseded_by IS NOT NULL "
                "AND invalidated_at IS NOT NULL AND invalidated_at < ?",
                (deadline,),
            ).fetchall()
            for row in expired:
                self.store.db.execute(
                    "DELETE FROM semantic_memory WHERE key = ?", (row["key"],)
                )
                count += 1
            if count:
                self.store.db.commit()
        except Exception:
            logger.debug("lint: superseded-purge failed", exc_info=True)
        self.report.auto_fixed["superseded_purged"] = count

    def load(self) -> None:
        rows = self.store.db.execute(
            "SELECT key, value_json, recall_count, updated_at FROM semantic_memory "
            "WHERE is_deleted = 0 AND key NOT LIKE 'lesson.%'"
        ).fetchall()
        self.facts = list(map(dict, rows))

    def stale(self) -> None:
        unrecalled = (
            fact for fact in self.facts if (fact.get("recall_count") or 0) == 0
        )
        for fact in unrecalled:
            updated = _parse_iso(fact.get("updated_at"))
            if updated is None:
                continue
            days = (self.now - updated).days
            if days >= _STALE_DAYS:
                self.report.add_flag("stale", fact["key"], f"not recalled in {days}d")

    def sparse(self) -> None:
        for fact in self.facts:
            value = fact["value_json"]
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            if isinstance(value, str) and len(value.strip()) < 2:
                self.report.add_flag("sparse", fact["key"], "near-empty value")

    def duplicates(self) -> None:
        lexemes = [_keywords(str(fact["value_json"])) for fact in self.facts]
        postings: dict[str, set[int]] = defaultdict(set)
        for index, words in enumerate(lexemes):
            for word in words:
                postings[word].add(index)
        for index, words in enumerate(lexemes):
            possible = set().union(*(postings[word] for word in words))
            for other_index in sorted(other for other in possible if other > index):
                other_words = lexemes[other_index]
                ratio = len(words.intersection(other_words)) / min(
                    len(words), len(other_words)
                )
                if ratio < _NEAR_DUP_RATIO:
                    continue
                first, second = self.facts[index]["key"], self.facts[other_index]["key"]
                pair = tuple(sorted((first, second)))
                if pair in self.related:
                    continue
                self.related.add(pair)
                self.report.add_flag(
                    "near_dup", first, f"~{ratio:.0%} overlap with {second}"
                )

    def contradictions(self) -> None:
        if self.judge is None:
            return
        values: dict = {}
        for fact in self.facts:
            values.setdefault(fact["key"], str(fact["value_json"]))
        for first, second in self.related:
            left, right = values.get(first, ""), values.get(second, "")
            if not (left and right):
                continue
            try:
                if self.judge(left, right):
                    self.report.add_flag(
                        "contradiction", first, f"contradicts {second}"
                    )
            except Exception:
                logger.debug(
                    "lint: contradiction judge failed for %s/%s",
                    first,
                    second,
                    exc_info=True,
                )

    def run(self, vault) -> LintReport:
        for stage in (
            self.expire_superseded,
            self.load,
            self.stale,
            self.sparse,
            self.duplicates,
            self.contradictions,
        ):
            stage()
        _lint_conflicts(self.store, self.report)
        _lint_graph(self.store, self.report)
        _lint_vault(vault, self.report)
        logger.info(
            "memory lint: auto-fixed %s, %d flags",
            self.report.auto_fixed,
            len(self.report.flags),
        )
        return self.report


def lint_memory(
    vs, *, now: datetime | None = None, judge=None, vault=None
) -> LintReport:
    selected_judge = (
        getattr(vs, "contradiction_judge", None) if judge is None else judge
    )
    return _MemorySweep(vs, now or datetime.now(tz=timezone.utc), selected_judge).run(
        vault
    )


def _lint_vault(vault, report: LintReport) -> None:
    if vault is not None:
        try:
            observations = vault.lint_flags()
        except Exception:
            logger.debug("lint: vault checks unavailable", exc_info=True)
            return
        for observation in observations:
            check, key, detail = observation
            report.add_flag(check, key, detail)


def _lint_conflicts(vs, report: LintReport) -> None:
    try:
        from gideon.cognition.memory_formation import conflicts
    except Exception:
        logger.debug("lint: conflict check unavailable", exc_info=True)
        return
    for conflict in conflicts(vs):
        explanation = [
            f"kept alongside {conflict['old_key']} — undecided contradiction"
        ]
        if conflict.get("reason"):
            explanation.append(f" ({conflict['reason']})")
        report.add_flag("keep_both", conflict["new_key"], "".join(explanation))


def _lint_graph(vs, report: LintReport) -> None:
    if not getattr(vs, "graph_enabled", False):
        return
    try:
        graph = vs.graph
        totals = graph.summary()
    except Exception:
        logger.debug("lint: graph checks unavailable", exc_info=True)
        return
    if totals["entities"] != 0:
        for kind in ("semantic", "episodic"):
            count = totals[f"{kind}_orphans"]
            if count:
                report.add_flag(
                    "graph_orphans", kind, f"{count} {kind} record(s) link to no entity"
                )
        count = totals["phantom_entities"]
        if count:
            report.add_flag(
                "phantom_entity",
                "entities",
                f"{count} entit(y/ies) have no inbound links — candidates for merge or removal",
            )
    for item in graph.proposals():
        detail = (
            f"mentioned in {item['mention_count']} records but not a known entity — "
            "accept it to start linking, or reject it"
        )
        report.add_flag("proposed_entity", item["name"], detail)
