"""Workflow work-state projection, claim decisions and section composition."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows.models import (
    TERMINAL_RUN_STATUSES,
    OriginKind,
    RunStatus,
)

logger = logging.getLogger(__name__)

MAX_LEASE_SECS = 3600

DEFAULT_LEASE_SECS = 900


class BoardState(str, Enum):
    NEEDS_INPUT = "needs_input"
    WORKING = "working"
    QUEUED = "queued"
    SUSPENDED = "suspended"
    REVIEW = "review"
    DONE = "done"


BOARD_ORDER = (
    BoardState.NEEDS_INPUT,
    BoardState.WORKING,
    BoardState.QUEUED,
    BoardState.SUSPENDED,
    BoardState.REVIEW,
    BoardState.DONE,
)

COLLAPSED_ORIGINS = frozenset({OriginKind.SUBAGENT_TOOL.value})

UNATTENDED_ORIGINS = frozenset({OriginKind.IDLE.value})


class Completeness(str, Enum):
    COMPLETE = "complete"
    INFERRED = "inferred"
    PARTIAL = "partial"
    ERROR = "error"


LEDGERS = {
    "decisions": (
        "one line per resolved gate or run outcome, linking the run — an index, not a store"
    ),
    "fog": "questions not yet precise enough to be tasks; promote when the question can be stated",
    "out_of_scope": "gist + reason + link; revisited only if the brief is redrawn",
}


@dataclass
class Claim:
    holder: str
    expires_at: float
    taken_at: float = 0.0
    renewals: int = 0

    def expired(self, now: float) -> bool:
        return self.expires_at <= now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClaimDecision:
    def __init__(self, existing, holder):
        self.existing, self.holder = existing, holder

    def acquire(self, now, ttl):
        if not self.holder:
            return None, "no holder"
        duration = min(MAX_LEASE_SECS, max(1, int(ttl)))
        active = self.existing is not None and not self.existing.expired(now)
        if active and self.existing.holder != self.holder:
            return (
                None,
                f"held by {self.existing.holder} for another {int(self.existing.expires_at - now)}s",
            )
        started = self.existing.taken_at or now if active else now
        renewals = self.existing.renewals + 1 if active else 0
        return Claim(self.holder, now + duration, started, renewals), ""

    def release(self):
        if self.existing is not None and self.existing.holder != self.holder:
            return self.existing, f"held by {self.existing.holder}, not {self.holder}"
        return None, ""


def claim(
    holder: str,
    *,
    now: float,
    ttl: int = DEFAULT_LEASE_SECS,
    existing: Claim | None = None,
) -> tuple[Claim | None, str]:
    return ClaimDecision(existing, holder).acquire(now, ttl)


def release(existing: Claim | None, holder: str) -> tuple[Claim | None, str]:
    return ClaimDecision(existing, holder).release()


@dataclass
class Substrate:
    kind: str = "inline"
    alive: bool = False
    detail: str = ""

    @property
    def isolated(self) -> bool:
        return self.kind in ("worktree", "container", "tmux")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SweepDecision:
    run_id: str
    status: RunStatus | None = None
    board_state: BoardState = BoardState.DONE
    reason: str = ""
    resumable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "status": self.status.value if self.status else "",
            "board_state": self.board_state.value,
        }


@dataclass
class BoardRow:
    run_id: str
    title: str
    state: BoardState
    origin: str = ""
    project_id: str = ""
    claim: Claim | None = None
    collapsed: bool = False
    attention: bool = False
    resumable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **{key: getattr(self, key) for key in ("run_id", "title")},
            "state": self.state.value,
            **{key: getattr(self, key) for key in ("origin", "project_id")},
            "claim": self.claim.to_dict() if self.claim else None,
            **{
                key: getattr(self, key)
                for key in ("collapsed", "attention", "resumable")
            },
        }


class RunProjection:
    def __init__(self, run):
        self.run = run

    def text(self, field):
        return str(getattr(self.run, field, "") or "")

    def state(self):
        status = getattr(self.run, "status", None)
        if status == RunStatus.RUNNING:
            return (
                BoardState.WORKING
                if getattr(self.run, "started_at", "")
                else BoardState.QUEUED
            )
        mapping = (
            (RunStatus.NEEDS_INPUT, BoardState.NEEDS_INPUT),
            (RunStatus.PAUSED, BoardState.SUSPENDED),
            (RunStatus.DRAFT, BoardState.QUEUED),
            (RunStatus.ESCALATED, BoardState.REVIEW),
        )
        return next(
            (state for value, state in mapping if status == value), BoardState.DONE
        )

    def sweep(self, substrate):
        status = getattr(self.run, "status", None)
        decision = SweepDecision(run_id=self.text("id"))
        if status in TERMINAL_RUN_STATUSES:
            decision.status, decision.board_state = status, board_state_for(self.run)
            decision.reason = "already terminal — left untouched"
        elif substrate.isolated and substrate.alive:
            decision.status, decision.board_state = (
                RunStatus.PAUSED,
                BoardState.SUSPENDED,
            )
            decision.reason = (
                f"{substrate.kind} substrate survived the restart — resumable"
            )
            decision.resumable = True
        else:
            decision.status = RunStatus.CANCELLED
            decision.reason = (
                "substrate is gone" if substrate.isolated else "server restarted"
            )
        return decision

    def board(self, claim_record, now):
        origin = _origin_kind(self.run)
        state = board_state_for(self.run)
        row = BoardRow(
            self.text("id"),
            self.text("workflow_name") or "(unnamed run)",
            state,
            origin=origin,
            project_id=self.text("project_id"),
            collapsed=origin in COLLAPSED_ORIGINS,
            attention=state is BoardState.NEEDS_INPUT
            and origin not in UNATTENDED_ORIGINS,
            resumable=state is BoardState.SUSPENDED,
        )
        if claim_record and not claim_record.expired(now):
            row.claim = claim_record
        return row


def durable_worker_name(run: Any) -> str:
    from gideon.engine import tmux_substrate

    record = RunProjection(run)
    return tmux_substrate.durable_session_name(
        record.text("project_id") or "default",
        record.text("id"),
        record.text("workflow_name") or "run",
    )


def sweep_decision(run: Any, substrate: Substrate) -> SweepDecision:
    return RunProjection(run).sweep(substrate)


def board_state_for(run: Any) -> BoardState:
    return RunProjection(run).state()


def board_row(
    run: Any, *, claim_record: Claim | None = None, now: float = 0.0
) -> BoardRow:
    return RunProjection(run).board(claim_record, now)


def _origin_kind(run: Any) -> str:
    origin = getattr(run, "origin", None)
    kind = getattr(origin, "kind", None)
    return kind.value if isinstance(kind, OriginKind) else str(kind or origin or "")


def attention_count(rows: list[BoardRow]) -> int:
    return sum(bool(row.attention) for row in rows)


def group_board(rows: list[BoardRow]) -> list[dict[str, Any]]:
    groups = {state: [] for state in BOARD_ORDER}
    for row in rows:
        if row.state in groups:
            groups[row.state].append(row)
    return [
        {
            "state": state.value,
            "count": len(group),
            "attention": attention_count(group),
            "rows": [row.to_dict() for row in group],
        }
        for state, group in groups.items()
        if group
    ]


@dataclass
class Section:
    name: str
    items: list[Any] = field(default_factory=list)
    status: str = "ok"
    error: str = ""
    loaded_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "items": list(self.items),
            "status": self.status,
            "error": self.error,
            "loadedAt": self.loaded_at,
        }


class SectionCollection:
    def __init__(self, sources, now):
        self.sources, self.now = sources, now
        self.sections, self.failures = [], 0

    def collect(self):
        for name, source in self.sources.items():
            section = Section(name=name, loaded_at=self.now)
            try:
                items = source() if callable(source) else source
                section.items = list(items or [])
            except Exception as exc:
                self.failures += 1
                section.status, section.error = "error", f"{type(exc).__name__}: {exc}"
                logger.debug("work section %r failed", name, exc_info=True)
            self.sections.append(section.to_dict())
        completeness = Completeness.COMPLETE
        if self.failures:
            completeness = (
                Completeness.ERROR
                if self.failures == len(self.sources)
                else Completeness.PARTIAL
            )
        return self.sections, completeness


def collect_sections(
    sources: dict[str, Any], *, now: float = 0.0
) -> tuple[list[dict[str, Any]], Completeness]:
    return SectionCollection(sources, now).collect()


def project_block(brief: str, overview: str, instructions: str) -> str:
    fields = (
        ("PROJECT BRIEF (the goal and scope of this effort):", brief),
        ("PROJECT OVERVIEW (current state — what the project now knows):", overview),
        ("PROJECT INSTRUCTIONS (how to operate here):", instructions),
    )
    return "\n\n".join(
        f"{label}\n{text.strip()}" for label, text in fields if (text or "").strip()
    )


def ledger_entry(
    kind: str, text: str, *, link: str = "", reason: str = ""
) -> dict[str, Any]:
    if kind not in LEDGERS:
        raise ValueError(f"unknown ledger {kind!r}; expected one of {sorted(LEDGERS)}")
    entry = {"kind": kind, "text": (text or "").strip()}
    if link:
        entry.update(link=link)
    if kind == "out_of_scope" and not (reason or "").strip():
        entry.update(reason="no reason recorded")
    elif reason:
        entry.update(reason=reason.strip())
    return entry
