"""Run-scoped approvals, owner-bound remote answers and bounded event holds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows.models import OriginKind
from gideon.integrations.tool_providers.base import RiskLevel

logger = logging.getLogger(__name__)

AUTO_APPROVABLE_RISKS = frozenset({RiskLevel.SAFE, RiskLevel.CAUTION})

UNATTENDED_ORIGINS = frozenset(
    {OriginKind.SCHEDULE, OriginKind.EVENT, OriginKind.HOOK, OriginKind.IDLE}
)

DEFAULT_EVENT_HOLD_LIMIT = 5


class Decision(str, Enum):
    """What the policy decided about a gate."""

    ASK = "ask"
    AUTO_APPROVED = "auto_approved"
    AUTO_DENIED = "auto_denied"
    REMEMBERED = "remembered"


@dataclass
class PolicyVerdict:
    decision: Decision
    reason: str = ""
    risk: str = ""

    @property
    def asks_human(self) -> bool:
        return self.decision == Decision.ASK

    @property
    def approved(self) -> bool:
        return self.decision in (Decision.AUTO_APPROVED, Decision.REMEMBERED)

    def to_dict(self) -> dict[str, Any]:
        return dict(
            decision=self.decision.value,
            **{name: getattr(self, name) for name in ("reason", "risk")},
        )


def gate_risk(node_config: dict[str, Any]) -> RiskLevel:
    label = str((node_config or {}).get("risk", "") or "").strip().lower()
    return next(
        (risk for risk in RiskLevel if risk.value == label), RiskLevel.DESTRUCTIVE
    )


def is_unattended(origin_kind: OriginKind, *, mode: str = "background") -> bool:
    """Is nobody watching this run?"""
    return origin_kind in UNATTENDED_ORIGINS and str(mode) != "blocking"


@dataclass
class AllowMemory:
    """Run-scoped "always allow", keyed by (operation, target)."""

    _allowed: set[tuple[str, str]] = field(default_factory=set)

    @staticmethod
    def key(node_config: dict[str, Any], node_id: str) -> tuple[str, str]:
        config = node_config or {}
        operation = next(
            (
                value
                for name in ("operation", "kind")
                if (value := config.get(name, ""))
            ),
            "approval",
        )
        return str(operation), str(config.get("target", "") or node_id)

    def remember(self, node_config: dict[str, Any], node_id: str) -> None:
        self._allowed.add(self.key(node_config, node_id))

    def allows(self, node_config: dict[str, Any], node_id: str) -> bool:
        return self.key(node_config, node_id) in self._allowed

    def clear(self) -> None:
        self._allowed.clear()

    def __len__(self) -> int:
        return len(self._allowed)


def decide(
    node_config: dict[str, Any],
    node_id: str,
    *,
    origin_kind: OriginKind = OriginKind.MANUAL,
    mode: str = "background",
    memory: AllowMemory | None = None,
) -> PolicyVerdict:
    risk = gate_risk(node_config)
    rules = (
        (
            lambda: memory is not None and memory.allows(node_config, node_id),
            lambda: (
                Decision.REMEMBERED,
                "the user chose 'always allow' for this operation in this run",
            ),
        ),
        (
            lambda: is_unattended(origin_kind, mode=mode),
            lambda: _unattended_verdict(risk),
        ),
    )
    for applies, outcome in rules:
        if applies():
            decision, reason = outcome()
            return PolicyVerdict(decision, reason, risk.value)
    return PolicyVerdict(Decision.ASK, "attended run", risk.value)


def owner_of(run: Any) -> str:
    """Who may answer this run's gates."""
    origin = getattr(run, "origin", None)
    return str(getattr(origin, "session_key", "") or "")


def may_answer(run: Any, *, responder: str, channel: str = "") -> tuple[bool, str]:
    if not channel:
        return True, ""
    owner = owner_of(run)
    if owner:
        allowed = bool(responder and responder == owner)
        return (
            (True, "")
            if allowed
            else (False, "only the run's requester may approve from a shared channel")
        )
    return False, "this run has no recorded owner, so remote approval is refused"


def remote_timeout_decision(node_config: dict[str, Any]) -> PolicyVerdict:
    """What an unanswered REMOTE gate becomes: DENY (WF2-R7)."""
    return PolicyVerdict(
        decision=Decision.AUTO_DENIED,
        reason="remote gate expired with no owner reply; silence is not consent",
        risk=gate_risk(node_config).value,
    )


@dataclass
class HoldState:
    """Re-hold accounting for one `gate{kind: event}`."""

    holds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"holds": self.holds}


@dataclass
class HoldVerdict:
    """The outcome of evaluating an event gate's prerequisite."""

    hold: bool = False
    preserve_event: bool = True
    give_up: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in ("hold", "preserve_event", "give_up", "reason")
        }


def evaluate_event_gate(
    node_config: dict[str, Any],
    state: HoldState,
    *,
    prerequisite_met: bool,
    input_valid: bool = True,
) -> HoldVerdict:
    return _EventHold(node_config, state).advance(prerequisite_met, input_valid)


def clarification_from_output(output: Any) -> dict[str, Any] | None:
    if isinstance(output, dict):
        value = output.get("needs_input") or output.get("clarification")
        if value:
            if isinstance(value, str):
                return dict(kind="text", prompt=value)
            if isinstance(value, dict):
                result = value.copy()
                for name, default in (
                    ("kind", "text"),
                    ("prompt", "The action needs more information."),
                ):
                    result.setdefault(name, default)
                return result
    return None


def _unattended_verdict(risk: RiskLevel) -> tuple[Decision, str]:
    if risk in AUTO_APPROVABLE_RISKS:
        return (
            Decision.AUTO_APPROVED,
            f"unattended run auto-approves {risk.value} gates",
        )
    return Decision.ASK, (
        f"{risk.value} gates always ask, even unattended — an unreviewed "
        "destructive action is worse than a stalled run"
    )


class _EventHold:
    def __init__(self, config: dict[str, Any], state: HoldState):
        raw = (config or {}).get("hold_limit")
        self.limit = (
            int(raw)
            if isinstance(raw, (int, float)) and raw > 0
            else DEFAULT_EVENT_HOLD_LIMIT
        )
        self.state = state

    def advance(self, ready: bool, valid: bool) -> HoldVerdict:
        if valid and not ready and not (self.state.holds >= self.limit):
            self.state.holds += 1
            return HoldVerdict(
                hold=True,
                preserve_event=True,
                reason=(
                    f"prerequisite absent (hold {self.state.holds}/{self.limit}); the wake-up event is "
                    "preserved for the next attempt"
                ),
            )
        if not valid:
            reason = "the event arrived but its payload was invalid — retrying the same input would only burn budget"
        elif ready:
            reason = "prerequisite satisfied"
        else:
            reason = f"prerequisite still absent after {self.state.holds} holds; giving up rather than waiting indefinitely"
        return HoldVerdict(
            preserve_event=False, give_up=not valid or not ready, reason=reason
        )
