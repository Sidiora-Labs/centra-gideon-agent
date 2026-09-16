"""Public curation records, journal and lifecycle operations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from gideon.cognition.learning.decay import DecayVerdict, active_days_between, evaluate

from .curation_cycle import (
    AgingSweep,
    CurationFindings,
    CuratorJournal,
    MutationProjection,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 8

MAX_CUT_FRACTION = 0.5
MIN_SET_FOR_REFUSAL = 8

STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"

DETECTOR_KINDS = (
    "compress_summary",
    "downgrade_detail",
    "promote_importance",
    "merge_candidates",
    "archive_unused",
)


@dataclass
class Mutation:
    """One reversible curator action, as journaled."""

    operation: str
    kind: str
    entity: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    at: str = ""
    undone_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        names = ("operation", "kind", "entity", "before", "after", "at", "undone_at")
        return {name: getattr(self, name) for name in names}


@dataclass
class CuratorReport:
    """What one pass did — or would have done, in dry-run."""

    mode: str = ""
    scanned: int = 0
    to_stale: list[str] = field(default_factory=list)
    to_archived: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    review_proposals: list[str] = field(default_factory=list)
    skipped_pinned: list[str] = field(default_factory=list)
    skipped_user: list[str] = field(default_factory=list)
    refused: str = ""
    dry_run: bool = False
    mutations: list[Mutation] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.to_stale) + len(self.to_archived) + len(self.reactivated)

    def summary(self) -> str:
        opening = f"curator[{self.mode or 'all'}]: scanned {self.scanned}"
        if self.refused:
            return opening + f" — REFUSED: {self.refused}"
        counts = (
            ("stale", self.to_stale),
            ("archived", self.to_archived),
            ("reactivated", self.reactivated),
            ("review", self.review_proposals),
            ("skipped-user", self.skipped_user),
        )
        parts = [opening] + [
            f"{label} {len(values)}" for label, values in counts if values
        ]
        if self.dry_run:
            parts += ["(dry run)"]
        return ", ".join(parts)


class MutationLog:
    """The undo journal. Append-only, in learning.db.

    Undo is what makes an automated janitor acceptable. Without it, every curator
    heuristic has to be right the first time on data the user cannot get back.
    """

    def __init__(self, base_dir: Any = None) -> None:
        from gideon.cognition.learning.staging import StagingStore

        self._staging, self._bootstrapped = StagingStore(base_dir), False
        self._journal = CuratorJournal(self)

    def _ensure(self) -> None:
        self._journal.ensure()

    def append(self, mutation: Mutation) -> int:
        return self._journal.append(mutation)

    def pending_undo(self, limit: int = 100) -> list[tuple[int, Mutation]]:
        return self._journal.pending(limit)

    def mark_undone(self, mutation_id: int) -> bool:
        return self._journal.mark(mutation_id)

    def changelog(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._journal.changelog(limit)

    def close(self) -> None:
        self._staging.close()


def _row_to_mutation(row: Any) -> Mutation:
    return MutationProjection().decode(row)


@dataclass
class Candidate:
    """One entity presented to the curator, with everything it needs to judge.

    A plain value object rather than a live handle: the curator decides, and the
    caller applies. That split is what lets the aging pass be tested without a
    skills loader, a template store, and a database.
    """

    kind: str
    entity: str
    state: str = STATE_ACTIVE
    last_used_at: str = ""
    created_at: str = ""
    importance: float = 0.0
    stability: float = 0.0
    pinned: bool = False
    source_type: str = "agent"
    linked_neighbors: int = 0
    audited_at: str = ""


def target_state(verdict: DecayVerdict, current: str) -> str:
    if not verdict.prune:
        if verdict.review:
            return current
        return STATE_STALE if verdict.strength < 0.5 else STATE_ACTIVE
    return STATE_ARCHIVED


def run_aging(
    candidates: list[Candidate],
    *,
    active_dates: list[str] | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    mode: str = "",
    batch_size: int = BATCH_SIZE,
    log: MutationLog | None = None,
) -> CuratorReport:
    sweep = AgingSweep(
        candidates,
        active_dates=active_dates,
        now=now,
        dry_run=dry_run,
        mode=mode,
        batch_size=batch_size,
        log=log,
    )
    return sweep.execute()


def _mutation(
    operation: str,
    entity: str,
    batch: list[Candidate],
    target: str,
    verdicts: dict[str, DecayVerdict],
) -> Mutation:
    return MutationProjection().create(operation, entity, batch, target, verdicts)


def file_review_proposals(report: CuratorReport, *, dry_run: bool = False) -> int:
    if dry_run:
        return 0
    return CurationFindings().file(
        report.review_proposals, dry_run=dry_run, promotion=False
    )


@dataclass
class PromotionSuggestion:
    """One entity that has EARNED a scope-widening suggestion, with the evidence.

    The evidence travels with the suggestion because widening scope is a trust
    decision: "used 4 times across 3 contexts, last used 2 active days ago" is
    reviewable, and "promote this?" is not.
    """

    kind: str
    entity: str
    why: str
    uses: int
    contexts: int
    idle_days: float

    def body(self) -> str:
        return (
            f"{self.entity} was captured session-scoped and has earned wider scope: "
            f"{self.uses} use(s) across {self.contexts} distinct context(s), last used "
            f"{self.idle_days:.0f} active day(s) ago. Multi-gate evidence, so this is a "
            "suggestion and not a promotion — scope widening is yours to decide."
        )


def promotion_suggestions(
    records: list[Any],
    *,
    active_dates: list[str] | None = None,
    now: datetime | None = None,
) -> list[PromotionSuggestion]:
    return CurationFindings().promotions(records, active_dates, now)


def file_promotion_suggestions(
    suggestions: list[PromotionSuggestion], *, dry_run: bool = False
) -> int:
    return CurationFindings().file(suggestions, dry_run=dry_run, promotion=True)


@dataclass
class Detection:
    """One optimizer finding, with a comparable saving estimate."""

    detector: str
    kind: str
    entity: str
    rationale: str
    estimated_token_saving: int = 0
    partner: str = ""


def detect(
    candidates: list[Candidate], *, sizes: dict[str, int] | None = None
) -> list[Detection]:
    return CurationFindings().detect(candidates, sizes)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
