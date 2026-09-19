from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gideon.automation.triggers.models import Outcome

GATE_ORDER: tuple[str, ...] = (
    "incident",
    "screen",
    "spacing",
    "rate",
    "quiet",
    "duty",
    "budget",
    "claim",
    "slot",
    "active",
    "yield",
    "capability",
)

GATE_OUTCOMES: dict[str, str] = {
    "incident": Outcome.REFUSED.value,
    "screen": Outcome.BLOCKED_INJECTION.value,
    "spacing": Outcome.SKIPPED_GATE.value,
    "rate": Outcome.SKIPPED_GATE.value,
    "quiet": Outcome.SKIPPED_GATE.value,
    "duty": Outcome.SKIPPED_GATE.value,
    "budget": Outcome.SKIPPED_BUDGET.value,
    "claim": Outcome.SKIPPED_OVERLAP.value,
    "slot": Outcome.DEFERRED.value,
    "active": Outcome.DEFERRED.value,
    "yield": Outcome.DEFERRED.value,
    "capability": Outcome.REFUSED.value,
}


@dataclass
class FireContext:
    trigger_id: str
    payload_text: str = ""
    gates: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] | None = None
    requested: dict[str, list[str]] = field(default_factory=dict)
    moment: datetime | None = None
    budget_remaining: float | None = None
    budget_readable: bool = True
    existing_claim: Any = None
    holder: str = ""
    overlap: str = "skip"
    now: float = 0.0
    user_active: bool = False
    yield_to_user: bool = False
    fires_in_window: int | None = None
    since_last_fire: float | None = None
    busy_slot: tuple[str, str] = ("", "")
    target_active: bool = False
    target_active_reason: str = ""


@dataclass
class FireDecision:
    allowed: bool
    outcome: str = Outcome.RAN.value
    gate: str = ""
    reason: str = ""
    claim: Any = None
    passed: list[str] = field(default_factory=list)
    violations: list[tuple[str, str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        fields: dict = {
            name: getattr(self, name)
            for name in ("allowed", "outcome", "gate", "reason")
        }
        return dict(
            fields,
            passed=list(self.passed),
            violations=list(map(list, self.violations)),
        )


def _refuse(gate: str, reason: str, passed: list[str], **extra: Any) -> FireDecision:
    fields: dict = dict(
        allowed=False,
        outcome=GATE_OUTCOMES[gate],
        gate=gate,
        reason=reason,
        passed=list(passed),
    )
    return FireDecision(**fields, **extra)


def _rate_refusal(ctx: FireContext) -> str:
    gates = ctx.gates if isinstance(ctx.gates, dict) else {}
    minimum = None
    for key in ("rate_cap", "max_runs_per_hour", "max_actions_per_hour"):
        try:
            cap = int(gates.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if cap > 0:
            minimum = cap if minimum is None else min(minimum, cap)
    if minimum is None or ctx.fires_in_window is None:
        return ""
    from gideon.automation.triggers.missed import within_rate_window

    permitted, reason = within_rate_window(
        fires_in_window=int(ctx.fires_in_window), max_per_hour=minimum
    )
    return "" if permitted else reason


def _spacing_refusal(ctx: FireContext) -> str:
    if ctx.since_last_fire is None:
        return ""
    gates = ctx.gates if isinstance(ctx.gates, dict) else {}
    for label in ("debounce", "cooldown"):
        try:
            window = float(gates.get(label + "_secs") or 0)
        except (TypeError, ValueError):
            continue
        since = ctx.since_last_fire
        if window > 0 and since < window:
            return f"{label} of {int(window)}s has {int(window - since)}s left (last fired {int(since)}s ago)"
    return ""


@dataclass
class GateWalk:
    context: FireContext
    passed: list[str] = field(default_factory=list)
    claim: Any = None
    moment: datetime | None = None

    def deny(self, gate: str, reason: str, **extra: Any) -> FireDecision:
        return _refuse(gate, reason, self.passed, claim=self.claim, **extra)

    def check_incident(self) -> FireDecision | None:
        from gideon.security.guardrails.incident import incident_active

        if incident_active():
            return self.deny(
                "incident",
                "incident mode is active: unattended fires are suspended (resume with `gideon incident off`)",
            )
        return None

    def check_screen(self) -> FireDecision | None:
        if self.context.payload_text:
            from gideon.automation.triggers.screen import screen

            result = screen(self.context.payload_text)
            if getattr(result, "verdict", "") == "blocked":
                groups = ", ".join(getattr(result, "groups", ()) or ()) or "injection"
                return self.deny(
                    "screen", f"payload blocked by the injection screen ({groups})"
                )
        self.moment = self.context.moment or datetime.now()
        return None

    def check_spacing(self) -> FireDecision | None:
        reason = _spacing_refusal(self.context)
        return self.deny("spacing", reason) if reason else None

    def check_rate(self) -> FireDecision | None:
        reason = _rate_refusal(self.context)
        return self.deny("rate", reason) if reason else None

    def check_quiet(self) -> FireDecision | None:
        from gideon.automation.triggers.calendar import evaluate_quiet

        moment = self.moment or datetime.now()
        result, _ = evaluate_quiet(self.context.gates, moment)
        return (
            None
            if result.allowed
            else self.deny("quiet", result.reason or "inside a quiet window")
        )

    async def check_duty(self) -> FireDecision | None:
        from gideon.automation.triggers.calendar import evaluate_duty

        moment = self.moment or datetime.now()
        result = await evaluate_duty(self.context.gates, moment)
        return (
            None
            if result.allowed
            else self.deny("duty", result.reason or "the duty gate refused")
        )

    def check_budget(self) -> FireDecision | None:
        if not self.context.budget_readable:
            return self.deny("budget", "budget could not be read; failing closed")
        available = self.context.budget_remaining
        if available is not None and available <= 0:
            return self.deny("budget", "budget exhausted for this window")
        return None

    def check_claim(self) -> FireDecision | None:
        from gideon.automation.triggers.scheduling import claim_fire

        ctx = self.context
        claim, reason = claim_fire(
            ctx.existing_claim,
            trigger_id=ctx.trigger_id,
            holder=ctx.holder or "firepath",
            now=ctx.now,
            overlap=ctx.overlap,
        )
        if claim is None:
            return self.deny("claim", reason or "another fire holds the claim")
        self.claim = claim
        return None

    def check_slot(self) -> FireDecision | None:
        slot, holder = self.context.busy_slot
        return (
            self.deny(
                "slot",
                f"resource slot {slot!r} is busy (held by {holder}); deferred until it frees",
            )
            if slot
            else None
        )

    def check_active(self) -> FireDecision | None:
        if self.context.target_active:
            return self.deny(
                "active",
                self.context.target_active_reason
                or "the target is active; deferred until it settles",
            )
        return None

    def check_yield(self) -> FireDecision | None:
        if self.context.yield_to_user and self.context.user_active:
            return self.deny("yield", "yielding to foreground user activity")
        return None

    def check_capability(self) -> FireDecision | None:
        if not self.context.requested:
            return None
        from gideon.automation.triggers.screen import (
            provider_is_read_only,
            unfenced_actions,
        )

        required = {}
        for category, values in self.context.requested.items():
            selected = [
                value
                for value in values
                if category != "providers" or not provider_is_read_only(value)
            ]
            if selected:
                required[category] = selected
        violations = (
            unfenced_actions(self.context.capabilities, requested=required)
            if required
            else []
        )
        if not violations:
            return None
        named = ", ".join(f"{kind}={value}" for kind, value, _ in violations[:3])
        return self.deny(
            "capability",
            f"action outside the frozen capability set: {named}",
            violations=violations,
        )

    async def run(self) -> FireDecision:
        for gate in GATE_ORDER:
            candidate = getattr(self, "check_" + gate)()
            decision = await candidate if inspect.isawaitable(candidate) else candidate
            if decision is not None:
                return decision
            self.passed.append(gate)
        return FireDecision(
            allowed=True,
            outcome=Outcome.RAN.value,
            claim=self.claim,
            passed=self.passed,
            reason="",
        )


async def evaluate(ctx: FireContext) -> FireDecision:
    return await GateWalk(ctx).run()


def ledger_row(decision: FireDecision, ctx: FireContext) -> dict[str, Any]:
    document = decision.to_dict()
    return dict(
        trigger_id=ctx.trigger_id,
        outcome=document["outcome"],
        reason=document["reason"],
        gate=document["gate"],
        gates_passed=document["passed"],
        violations=document["violations"],
    )


def suppressed_at(decision: FireDecision) -> str:
    return decision.gate if not decision.allowed else ""


def gate_order_is_intact(order: tuple[str, ...] = GATE_ORDER) -> list[str]:
    undeclared = [
        f"{name}: declared in the order with no typed outcome"
        for name in order
        if name not in GATE_OUTCOMES
    ]
    unused = [
        f"{name}: has an outcome but is not in the order"
        for name in GATE_OUTCOMES
        if name not in order
    ]
    return undeclared + unused
