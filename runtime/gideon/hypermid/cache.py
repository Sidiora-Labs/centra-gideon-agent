from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

BoundaryReason = Literal[
    "cache_expiry",
    "provider_profile_change",
    "incompatible_policy_revision",
    "identity_rebind",
    "explicit_flush",
    "tail_pressure",
]
CachedChange = Literal["none", "reduction", "tier_decay"]


class CachePolicyViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CachedRegion:
    digest: str
    bytes: bytes
    item_ids: tuple[str, ...] = ()
    summary_ids: tuple[str, ...] = ()
    token_mass: int = 0


@dataclass(frozen=True, slots=True)
class CacheGeneration:
    generation: int
    provider_profile_digest: str
    policy_revision: int
    baseline: CachedRegion
    delta: CachedRegion
    live_tail: CachedRegion
    boundary_reason: BoundaryReason | None = None


@dataclass(frozen=True, slots=True)
class CacheTransition:
    next: CacheGeneration
    cached_change: CachedChange = "none"
    boundary: BoundaryReason | None = None
    overflow_requires_cached_change: bool = False


@dataclass(frozen=True, slots=True)
class CacheOutcome:
    kind: Literal["applied", "deferred", "pressure_refused"]
    generation: int
    reason_code: str


def evaluate_transition(
    current: CacheGeneration | None, transition: CacheTransition
) -> CacheOutcome:
    next_state = transition.next
    if next_state.generation < 1 or next_state.policy_revision < 1:
        raise CachePolicyViolation(
            "INVALID_GENERATION",
            "cache generation and policy revision must be positive",
        )
    if current is None:
        return CacheOutcome("applied", next_state.generation, "cache_initialized")
    if transition.cached_change != "none" and transition.boundary is None:
        if transition.overflow_requires_cached_change:
            return CacheOutcome(
                "pressure_refused", current.generation, "cached_change_pressure_refused"
            )
        return CacheOutcome("deferred", current.generation, "cached_change_deferred")
    if transition.boundary is None:
        if next_state.generation != current.generation:
            raise CachePolicyViolation(
                "GENERATION_CHANGED_WITHOUT_BOUNDARY",
                "generation changed outside a boundary",
            )
        if next_state.provider_profile_digest != current.provider_profile_digest:
            raise CachePolicyViolation(
                "PROFILE_CHANGED_WITHOUT_BOUNDARY",
                "provider profile changed outside a boundary",
            )
        if next_state.policy_revision != current.policy_revision:
            raise CachePolicyViolation(
                "POLICY_CHANGED_WITHOUT_BOUNDARY", "policy changed outside a boundary"
            )
        if (
            next_state.baseline.bytes != current.baseline.bytes
            or next_state.baseline.digest != current.baseline.digest
        ):
            raise CachePolicyViolation(
                "BASELINE_CHANGED_WITHOUT_BOUNDARY",
                "baseline changed outside a boundary",
            )
        return CacheOutcome("applied", current.generation, "delta_refreshed")
    if next_state.generation <= current.generation:
        raise CachePolicyViolation(
            "GENERATION_NOT_ADVANCED", "baseline fold must advance generation"
        )
    if (
        next_state.delta.bytes
        or next_state.delta.item_ids
        or next_state.delta.summary_ids
        or next_state.delta.token_mass
    ):
        raise CachePolicyViolation(
            "DELTA_NOT_EMPTY_AFTER_FOLD", "baseline fold must begin with empty delta"
        )
    return CacheOutcome("applied", next_state.generation, "baseline_folded")


class CacheStore:
    def __init__(self) -> None:
        self._state: CacheGeneration | None = None
        self._lock = threading.RLock()

    def apply(self, transition: CacheTransition) -> CacheOutcome:
        with self._lock:
            outcome = evaluate_transition(self._state, transition)
            if outcome.kind == "applied":
                self._state = transition.next
            return outcome

    def snapshot(self) -> CacheGeneration | None:
        with self._lock:
            return self._state

    def replay_prefix(self) -> tuple[bytes, bytes] | None:
        with self._lock:
            if self._state is None:
                return None
            return self._state.baseline.bytes, self._state.delta.bytes
