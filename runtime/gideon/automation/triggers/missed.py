"""Bounded restart review and explicitly requested catch-up execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gideon.automation.triggers.models import Outcome
from gideon.automation.triggers.scheduling import (
    BOOT_STAGGER_BASE_SECS,
    BOOT_STAGGER_WINDOW_SECS,
    IntervalGrid,
    jitter_offset,
)

ENUMERATION_CAP = 480
REVIEW_ROWS_PER_TRIGGER = 20
CATCHUP_ORIGIN = "catchup"


@dataclass
class MissedSlot:
    trigger_id: str
    scheduled_for: float

    def to_dict(self) -> dict[str, Any]:
        return dict(trigger_id=self.trigger_id, scheduled_for=self.scheduled_for)


@dataclass
class MissedSummary:
    trigger_id: str
    count: int
    oldest: float
    newest: float

    def to_dict(self) -> dict[str, Any]:
        return dict(
            trigger_id=self.trigger_id,
            count=self.count,
            oldest=self.oldest,
            newest=self.newest,
        )


@dataclass
class MissedReview:
    rows: list[MissedSlot] = field(default_factory=list)
    summaries: list[MissedSummary] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(
            rows=list(map(MissedSlot.to_dict, self.rows)),
            summaries=list(map(MissedSummary.to_dict, self.summaries)),
            truncated=self.truncated,
        )


@dataclass(frozen=True)
class MissedWindow:
    identity: str
    anchor: float
    interval: float
    count: int

    def stamp(self, index: int) -> float:
        return self.anchor + index * self.interval

    def project(
        self, capacity: int
    ) -> tuple[list[MissedSlot], MissedSummary | None, int]:
        visible = min(self.count, capacity)
        omitted = self.count - visible
        cards = [
            MissedSlot(self.identity, self.stamp(index))
            for index in range(omitted, self.count)
        ]
        summary = None
        if omitted:
            summary = MissedSummary(
                self.identity, omitted, self.stamp(1), self.stamp(omitted)
            )
        return cards, summary, visible


class RecoveryInputs:
    def __init__(self, document: dict[str, Any], observed_at: float):
        self.document = document
        self.observed_at = observed_at
        self._armed: float | None = None

    @staticmethod
    def number(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def armed(self) -> float:
        if self._armed is None:
            from gideon.automation.triggers.service import to_epoch

            self._armed = to_epoch(str(self.document.get("next_fire_at", "") or ""))
        return self._armed

    def recurrence(self) -> float:
        period = self.number(self.document.get("interval_secs"))
        if period > 0:
            return period
        return self.number((self.document.get("spec") or {}).get("interval_secs"))

    def project(self) -> dict[str, Any]:
        period = self.recurrence()
        previous = self.number(self.document.get("last_fire_at"))
        if previous <= 0 and period > 0 and self.armed > 0:
            previous = self.armed - period
        if "missed_last_slot" in self.document:
            missed = bool(self.document.get("missed_last_slot"))
        else:
            missed = (
                self.observed_at > 0
                and self.armed > 0
                and self.armed < self.observed_at
            )
        if "fires_automatically" in self.document:
            automatic = bool(self.document.get("fires_automatically"))
        else:
            automatic = bool(self.document.get("enabled")) and str(
                self.document.get("state", "active") or "active"
            ) in ("active", "")
        return dict(
            interval_secs=period,
            last_fire_at=previous,
            missed_last_slot=missed,
            fires_automatically=automatic,
        )

    def catch_up(self, identity: str) -> tuple[str, float, str]:
        fields = self.project()
        refusal = ""
        if not self.document.get("catch_up"):
            refusal = "catch_up is off: a missed slot is reviewed, not re-run"
        elif not fields["missed_last_slot"]:
            refusal = "nothing was missed"
        elif not fields["fires_automatically"]:
            refusal = "disabled or paused: a catch-up must not restart it"
        if refusal:
            return identity, 0.0, refusal
        scheduled = self.observed_at + BOOT_STAGGER_BASE_SECS
        scheduled += jitter_offset(identity, BOOT_STAGGER_WINDOW_SECS)
        return identity, scheduled, CATCHUP_ORIGIN


def _ordered(triggers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(triggers, key=lambda entry: str(entry.get("id", "")))


def enumerate_missed(
    *,
    trigger_id: str,
    last_fire_at: float,
    interval_secs: float,
    now: float,
    budget: int = ENUMERATION_CAP,
    review_rows: int = REVIEW_ROWS_PER_TRIGGER,
) -> tuple[list[MissedSlot], MissedSummary | None, int]:
    if interval_secs <= 0 or last_fire_at <= 0 or now <= last_fire_at:
        return [], None, 0
    missed = int((now - last_fire_at) // interval_secs)
    if missed <= 0:
        return [], None, 0
    capacity = min(max(0, review_rows), max(0, budget))
    return MissedWindow(trigger_id, last_fire_at, interval_secs, missed).project(
        capacity
    )


def missed_inputs(entry: dict[str, Any], *, now: float = 0.0) -> dict[str, Any]:
    return RecoveryInputs(entry, now).project()


def review_at_boot(
    triggers: list[dict[str, Any]], *, now: float, budget: int = ENUMERATION_CAP
) -> MissedReview:
    result = MissedReview()
    remaining = max(0, budget)
    for document in _ordered(triggers):
        if remaining <= 0:
            result.truncated = True
            break
        values = missed_inputs(document, now=now)
        cards, summary, allocated = enumerate_missed(
            trigger_id=str(document.get("id", "") or ""),
            last_fire_at=values["last_fire_at"],
            interval_secs=values["interval_secs"],
            now=now,
            budget=remaining,
        )
        remaining -= allocated
        result.rows.extend(cards)
        if summary is not None:
            result.summaries.append(summary)
    return result


def late_outcome(
    outcome: str, *, scheduled_for: float, started_at: float
) -> tuple[str, str]:
    from gideon.automation.triggers.scheduling import LATE_THRESHOLD_SECS

    unchanged = outcome, ""
    if outcome != Outcome.RAN.value:
        return unchanged
    try:
        expected, actual = (
            float(value or 0.0) for value in (scheduled_for, started_at)
        )
    except (TypeError, ValueError):
        return unchanged
    if expected <= 0 or actual <= 0:
        return unchanged
    elapsed = actual - expected
    if elapsed < LATE_THRESHOLD_SECS:
        return unchanged
    return (
        Outcome.RAN_LATE.value,
        f"ran {int(elapsed // 60)} min after its scheduled slot",
    )


_REVIEW_ACTIONS = {
    "run_now": (
        Outcome.RAN_LATE.value,
        "ran from a missed-fire review card, after its scheduled slot",
    ),
    "dismiss": (
        Outcome.SKIPPED_MISSED.value,
        "the user dismissed the missed-fire card",
    ),
}


def resolve_missed(action: str) -> tuple[str, str]:
    for choice, receipt in _REVIEW_ACTIONS.items():
        if action == choice:
            return receipt
    return (
        Outcome.REFUSED.value,
        f"unknown review action {action!r}; expected run_now or dismiss",
    )


def catch_up_plan(
    triggers: list[dict[str, Any]], *, now: float
) -> list[tuple[str, float, str]]:
    return [
        RecoveryInputs(document, now).catch_up(str(document.get("id", "") or ""))
        for document in _ordered(triggers)
    ]


def within_rate_window(
    *, fires_in_window: int, max_per_hour: int, manual: bool = False
) -> tuple[bool, str]:
    if manual or max_per_hour <= 0:
        return True, (
            "manual fires bypass the hourly cap"
            if manual
            else "no hourly cap configured"
        )
    exhausted = fires_in_window >= max_per_hour
    reason = (
        f"{fires_in_window} fires in the last hour reaches the cap of {max_per_hour}"
        if exhausted
        else ""
    )
    return not exhausted, reason


def roll_forward(*, next_fire_at: float, interval_secs: float, now: float) -> float:
    if interval_secs <= 0:
        return next_fire_at
    if next_fire_at <= 0:
        return 0.0
    return (
        next_fire_at
        if next_fire_at > now
        else IntervalGrid(interval_secs, next_fire_at).after(now)
    )
