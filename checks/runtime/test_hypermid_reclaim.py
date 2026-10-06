from __future__ import annotations

import pytest

from gideon.hypermid.foundation import Digest
from gideon.hypermid.pressure import (
    PressureBand,
    PressureError,
    PressureLatch,
    PressurePolicy,
    PressureSnapshot,
    ReclaimDisposition,
    calibrate_pressure,
    parse_reclaim_plan,
)


def _snapshot(mass: int = 110) -> PressureSnapshot:
    return PressureSnapshot(
        calibrated_mass=mass,
        safe_input_mass=100,
        cache_generation=8,
        projection_digest=Digest.sha256(b"projection-8"),
        policy_revision=4,
    )


def _plan(
    *,
    disposition: str,
    savings: int,
    price: int = 0,
    decision: bytes = b"decision-1",
) -> dict[str, object]:
    choices = []
    if savings:
        choice: dict[str, object] = {
            "candidate_id": "candidate-1",
            "recoverable_mass": savings,
        }
        if price:
            choice["rewrite_cost_nanodollars"] = price
        choices.append(choice)
    return {
        "decision_digest": str(Digest.sha256(decision)),
        "band": "hard_wall",
        "disposition": disposition,
        "input_mass": 110,
        "target_mass": 100,
        "required_savings": 10,
        "estimated_savings": savings,
        "rewrite_cost_nanodollars": price,
        "chosen": choices,
        "reason_codes": ["pressure_hard_wall"],
    }


def test_pressure_gate_latches_priced_decisions_and_refuses_unsafe_overflow() -> None:
    snapshot = _snapshot()
    assert calibrate_pressure(snapshot) is PressureBand.HARD_WALL

    refused = parse_reclaim_plan(_plan(disposition="refused", savings=3), snapshot)
    assert refused.disposition is ReclaimDisposition.REFUSED
    assert not refused.dispatch_allowed
    with pytest.raises(PressureError) as refusal:
        refused.require_dispatch()
    assert refusal.value.code == "CONTEXT_HARD_WALL"

    policy = PressurePolicy(max_rewrite_cost_nanodollars=100)
    admitted = parse_reclaim_plan(
        _plan(disposition="applied", savings=10, price=100),
        snapshot,
        policy,
    )
    latch = PressureLatch()
    first = latch.admit(snapshot, admitted)
    replay = latch.admit(snapshot, admitted)
    assert not first.replayed
    assert replay.replayed
    admitted.require_dispatch()

    changed = parse_reclaim_plan(
        _plan(
            disposition="applied",
            savings=10,
            price=100,
            decision=b"decision-2",
        ),
        snapshot,
        policy,
    )
    with pytest.raises(PressureError) as latched:
        latch.admit(snapshot, changed)
    assert latched.value.code == "DECISION_LATCHED"

    with pytest.raises(PressureError) as over_budget:
        parse_reclaim_plan(
            _plan(disposition="applied", savings=10, price=101),
            snapshot,
            policy,
        )
    assert over_budget.value.code == "REWRITE_BUDGET_EXCEEDED"


def test_pressure_bands_use_integer_calibrated_provider_mass() -> None:
    assert calibrate_pressure(_snapshot(69)) is PressureBand.NORMAL
    assert calibrate_pressure(_snapshot(70)) is PressureBand.ADVISORY
    assert calibrate_pressure(_snapshot(82)) is PressureBand.ACTION
    assert calibrate_pressure(_snapshot(93)) is PressureBand.EMERGENCY
    assert calibrate_pressure(_snapshot(101)) is PressureBand.HARD_WALL
