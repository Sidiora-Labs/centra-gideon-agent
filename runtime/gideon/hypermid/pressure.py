"""Pressure admission, priced rewrite validation, and reclaim decision latching."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .foundation import Digest


class PressureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PressureBand(str, Enum):
    NORMAL = "normal"
    ADVISORY = "advisory"
    ACTION = "action"
    EMERGENCY = "emergency"
    HARD_WALL = "hard_wall"


class ReclaimDisposition(str, Enum):
    DIAGNOSTIC = "diagnostic"
    APPLIED = "applied"
    DEFERRED = "deferred"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class PressurePolicy:
    advisory_basis_points: int = 7_000
    action_basis_points: int = 8_200
    emergency_basis_points: int = 9_300
    max_rewrite_cost_nanodollars: int | None = None

    def __post_init__(self) -> None:
        if not (
            0
            < self.advisory_basis_points
            < self.action_basis_points
            < self.emergency_basis_points
            < 10_000
        ):
            raise PressureError(
                "INVALID_POLICY", "pressure thresholds must be strictly ordered"
            )
        if self.max_rewrite_cost_nanodollars is not None and (
            isinstance(self.max_rewrite_cost_nanodollars, bool)
            or self.max_rewrite_cost_nanodollars < 0
        ):
            raise PressureError(
                "INVALID_POLICY", "rewrite budget must be a non-negative integer"
            )


@dataclass(frozen=True, slots=True)
class PressureSnapshot:
    calibrated_mass: int
    safe_input_mass: int
    cache_generation: int
    projection_digest: Digest
    policy_revision: int

    def __post_init__(self) -> None:
        for field, value in (
            ("calibrated_mass", self.calibrated_mass),
            ("safe_input_mass", self.safe_input_mass),
            ("cache_generation", self.cache_generation),
            ("policy_revision", self.policy_revision),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PressureError("INVALID_PRESSURE", f"{field} must be non-negative")
        if self.safe_input_mass == 0:
            raise PressureError("INVALID_PRESSURE", "safe input mass must be positive")
        object.__setattr__(self, "projection_digest", Digest(self.projection_digest))


@dataclass(frozen=True, slots=True)
class ReclaimChoice:
    candidate_id: str
    recoverable_mass: int
    rewrite_cost_nanodollars: int | None


@dataclass(frozen=True, slots=True)
class ReclaimPlan:
    decision_digest: Digest
    band: PressureBand
    disposition: ReclaimDisposition
    input_mass: int
    target_mass: int
    required_savings: int
    estimated_savings: int
    rewrite_cost_nanodollars: int
    choices: tuple[ReclaimChoice, ...]
    reason_codes: tuple[str, ...]

    @property
    def dispatch_allowed(self) -> bool:
        return self.disposition is not ReclaimDisposition.REFUSED

    def require_dispatch(self) -> None:
        if not self.dispatch_allowed:
            raise PressureError(
                "CONTEXT_HARD_WALL",
                "safe reclaim could not fit the provider input budget",
            )


@dataclass(frozen=True, slots=True)
class LatchedDecision:
    snapshot: PressureSnapshot
    plan: ReclaimPlan
    replayed: bool


def calibrate_pressure(
    snapshot: PressureSnapshot, policy: PressurePolicy = PressurePolicy()
) -> PressureBand:
    if snapshot.calibrated_mass > snapshot.safe_input_mass:
        return PressureBand.HARD_WALL
    basis_points = snapshot.calibrated_mass * 10_000 // snapshot.safe_input_mass
    if basis_points >= policy.emergency_basis_points:
        return PressureBand.EMERGENCY
    if basis_points >= policy.action_basis_points:
        return PressureBand.ACTION
    if basis_points >= policy.advisory_basis_points:
        return PressureBand.ADVISORY
    return PressureBand.NORMAL


def parse_reclaim_plan(
    value: object,
    snapshot: PressureSnapshot,
    policy: PressurePolicy = PressurePolicy(),
) -> ReclaimPlan:
    if not isinstance(value, Mapping):
        raise PressureError("INVALID_PLAN", "reclaim plan must be an object")
    required = {
        "decision_digest",
        "band",
        "disposition",
        "input_mass",
        "target_mass",
        "required_savings",
        "estimated_savings",
        "rewrite_cost_nanodollars",
        "chosen",
        "reason_codes",
    }
    optional = {"estimated_bytes", "exclusions", "boundary"}
    if set(value) - required - optional or not required <= set(value):
        raise PressureError(
            "INVALID_PLAN", "reclaim plan fields do not match the contract"
        )
    try:
        band = PressureBand(value["band"])
        disposition = ReclaimDisposition(value["disposition"])
        decision_digest = Digest(value["decision_digest"])
    except (TypeError, ValueError) as exc:
        raise PressureError(
            "INVALID_PLAN", "reclaim plan identity or state is invalid"
        ) from exc
    integers: dict[str, int] = {}
    for field in (
        "input_mass",
        "target_mass",
        "required_savings",
        "estimated_savings",
        "rewrite_cost_nanodollars",
    ):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise PressureError(
                "INVALID_PLAN", f"{field} must be a non-negative integer"
            )
        integers[field] = item
    if integers["input_mass"] != snapshot.calibrated_mass:
        raise PressureError(
            "STALE_PLAN", "reclaim plan was priced for another input mass"
        )
    if band is not calibrate_pressure(snapshot, policy):
        raise PressureError("STALE_PLAN", "reclaim plan uses the wrong pressure band")
    if integers["required_savings"] != max(
        0, integers["input_mass"] - integers["target_mass"]
    ):
        raise PressureError(
            "INVALID_PLAN", "required savings do not match the target mass"
        )
    choices_raw = value["chosen"]
    if not isinstance(choices_raw, list) or len(choices_raw) > 100_000:
        raise PressureError("INVALID_PLAN", "chosen candidates exceed the result bound")
    choices: list[ReclaimChoice] = []
    priced_total = 0
    for index, item in enumerate(choices_raw):
        if not isinstance(item, Mapping):
            raise PressureError("INVALID_PLAN", f"chosen[{index}] must be an object")
        candidate_id = item.get("candidate_id")
        mass = item.get("recoverable_mass")
        price = item.get("rewrite_cost_nanodollars")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise PressureError(
                "INVALID_PLAN", f"chosen[{index}] has no candidate identity"
            )
        if isinstance(mass, bool) or not isinstance(mass, int) or mass <= 0:
            raise PressureError(
                "INVALID_PLAN", f"chosen[{index}] has invalid recoverable mass"
            )
        if price is not None and (
            isinstance(price, bool) or not isinstance(price, int) or price <= 0
        ):
            raise PressureError(
                "UNPRICED_REWRITE", "rewrite price must be a positive exact integer"
            )
        priced_total += price or 0
        choices.append(ReclaimChoice(candidate_id, mass, price))
    if priced_total != integers["rewrite_cost_nanodollars"]:
        raise PressureError(
            "INVALID_PLAN", "rewrite price total does not match chosen work"
        )
    if priced_total and (
        policy.max_rewrite_cost_nanodollars is None
        or priced_total > policy.max_rewrite_cost_nanodollars
    ):
        raise PressureError(
            "REWRITE_BUDGET_EXCEEDED", "priced rewrite exceeds its authorized budget"
        )
    if integers["estimated_savings"] != sum(
        choice.recoverable_mass for choice in choices
    ):
        raise PressureError(
            "INVALID_PLAN", "estimated savings do not match chosen candidates"
        )
    if band is PressureBand.HARD_WALL:
        safe = integers["estimated_savings"] >= integers["required_savings"]
        if safe == (disposition is ReclaimDisposition.REFUSED):
            raise PressureError(
                "UNSAFE_PLAN", "hard-wall disposition does not match safe savings"
            )
    reasons = value["reason_codes"]
    if (
        not isinstance(reasons, list)
        or not reasons
        or not all(isinstance(reason, str) and reason for reason in reasons)
    ):
        raise PressureError("INVALID_PLAN", "reclaim plan must contain reason codes")
    return ReclaimPlan(
        decision_digest=decision_digest,
        band=band,
        disposition=disposition,
        input_mass=integers["input_mass"],
        target_mass=integers["target_mass"],
        required_savings=integers["required_savings"],
        estimated_savings=integers["estimated_savings"],
        rewrite_cost_nanodollars=integers["rewrite_cost_nanodollars"],
        choices=tuple(choices),
        reason_codes=tuple(reasons),
    )


class PressureLatch:
    """Freezes one priced decision for each immutable projection state."""

    def __init__(self) -> None:
        self._decisions: dict[tuple[Digest, int, int, int, int], ReclaimPlan] = {}

    def admit(self, snapshot: PressureSnapshot, plan: ReclaimPlan) -> LatchedDecision:
        if plan.input_mass != snapshot.calibrated_mass:
            raise PressureError(
                "STALE_PLAN", "plan does not match the pressure snapshot"
            )
        key = (
            snapshot.projection_digest,
            snapshot.cache_generation,
            snapshot.policy_revision,
            snapshot.calibrated_mass,
            snapshot.safe_input_mass,
        )
        current = self._decisions.get(key)
        if current is not None:
            if current != plan:
                raise PressureError(
                    "DECISION_LATCHED",
                    "a different reclaim decision is already latched for this projection state",
                )
            return LatchedDecision(snapshot, current, True)
        self._decisions[key] = plan
        return LatchedDecision(snapshot, plan, False)


__all__ = [
    "LatchedDecision",
    "PressureBand",
    "PressureError",
    "PressureLatch",
    "PressurePolicy",
    "PressureSnapshot",
    "ReclaimChoice",
    "ReclaimDisposition",
    "ReclaimPlan",
    "calibrate_pressure",
    "parse_reclaim_plan",
]
