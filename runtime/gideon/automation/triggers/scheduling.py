"""Timing, restart recovery and admission decisions for trigger execution."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

POLL_CEILING_SECS = 30.0
BOOT_STAGGER_BASE_SECS = 60.0
BOOT_STAGGER_WINDOW_SECS = 120.0
LATE_THRESHOLD_SECS = 2 * (
    BOOT_STAGGER_BASE_SECS + BOOT_STAGGER_WINDOW_SECS + POLL_CEILING_SECS
)
CLAIM_MAX_DURATION_SECS = 3600.0
TIMER_CEILING_SECS = 24 * 24 * 3600.0


class Dueness(str, Enum):
    DUE = "due"
    NOT_YET = "not_yet"
    NOT_ARMED = "not_armed"
    DISABLED = "disabled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ScheduledWake:
    timestamp: float
    observed_at: float

    @property
    def position(self) -> Dueness:
        if self.timestamp <= 0:
            return Dueness.NOT_ARMED
        return Dueness.NOT_YET if self.observed_at < self.timestamp else Dueness.DUE

    def admission(self, automatic: bool, expiry: float) -> Dueness:
        if not automatic:
            return Dueness.DISABLED
        if expiry and self.observed_at >= expiry:
            return Dueness.EXPIRED
        return self.position

    def recover(self, identity: str, catch_up: bool) -> tuple[float, str]:
        state = self.position
        if state is Dueness.NOT_ARMED:
            return 0.0, "not_armed"
        if state is Dueness.NOT_YET:
            return self.timestamp, "still_upcoming"
        ready_at = self.observed_at + BOOT_STAGGER_BASE_SECS
        ready_at += jitter_offset(identity, BOOT_STAGGER_WINDOW_SECS)
        return ready_at, ("caught_up_staggered" if catch_up else "missed_dropped")


@dataclass(frozen=True)
class IntervalGrid:
    period: float
    origin: float

    def after(self, timestamp: float) -> float:
        if self.period <= 0:
            return 0.0
        epoch = self.origin if self.origin > 0 else timestamp
        index = int((timestamp - epoch) // self.period)
        return epoch + (index + 1) * self.period


@dataclass
class Claim:
    trigger_id: str
    holder: str
    claimed_at: float
    max_duration_secs: float = CLAIM_MAX_DURATION_SECS

    def expired(self, now: float) -> bool:
        deadline = self.claimed_at + max(1.0, self.max_duration_secs)
        return now >= deadline

    def to_dict(self) -> dict[str, Any]:
        fields = asdict(self)
        fields["expires_at"] = self.claimed_at + self.max_duration_secs
        return fields


def jitter_offset(trigger_id: str, window: float) -> float:
    if window <= 0:
        return 0.0
    identity = (trigger_id or "").encode("utf-8")
    fingerprint = hashlib.blake2b(identity, digest_size=8)
    rank = int.from_bytes(fingerprint.digest(), byteorder="big")
    return (rank / float(1 << 64)) * window


def is_due(
    *,
    next_fire_at: float,
    now: float,
    fires_automatically: bool,
    expires_at: float = 0.0,
) -> tuple[bool, str]:
    state = ScheduledWake(next_fire_at, now).admission(fires_automatically, expires_at)
    return state is Dueness.DUE, state.value


def next_wake_delay(next_fires: list[float], now: float) -> float:
    earliest = min((value for value in next_fires if value > 0), default=None)
    if earliest is None:
        return POLL_CEILING_SECS
    remaining = earliest - now
    return max(0.0, min(remaining, POLL_CEILING_SECS, TIMER_CEILING_SECS))


def recompute_from_completion(
    *, interval_secs: float, created_at: float, completed_at: float
) -> float:
    return IntervalGrid(interval_secs, created_at).after(completed_at)


def boot_recovery(
    *, next_fire_at: float, now: float, trigger_id: str, catch_up: bool
) -> tuple[float, str]:
    return ScheduledWake(next_fire_at, now).recover(trigger_id, catch_up)


def claim_fire(
    existing: Claim | None,
    *,
    trigger_id: str,
    holder: str,
    now: float,
    overlap: str = "skip",
) -> tuple[Claim | None, str]:
    if overlap != "parallel" and existing is not None and not existing.expired(now):
        age = int(now - existing.claimed_at)
        return None, f"held by {existing.holder} since {age}s ago"
    return Claim(trigger_id, holder, now), ""


def coalesce_wakes(
    next_fires: dict[str, float], now: float, window_secs: float = 1.0
) -> list[str]:
    horizon = now + window_secs
    batch = (key for key, timestamp in next_fires.items() if 0 < timestamp <= horizon)
    return sorted(batch, key=lambda key: (next_fires[key], key))


def revalidate(
    *, still_enabled: bool, next_fire_at_at_arm: float, next_fire_at_now: float
) -> tuple[bool, str]:
    reason = ""
    if not still_enabled:
        reason = "disabled while the timer slept"
    elif next_fire_at_now != next_fire_at_at_arm:
        reason = "rescheduled while the timer slept"
    return not reason, reason
