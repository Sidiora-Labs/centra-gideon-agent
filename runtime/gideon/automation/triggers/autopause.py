"""Run exit classification, automatic state transitions and attention cards."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from gideon.automation.triggers.models import (
    TRUE_FAILURE_OUTCOMES,
    Outcome,
    TriggerHealth,
    TriggerState,
)

FAILURE_BUDGET = 5

PARK_COOLDOWN_SECS = 300.0


class ExitType(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    AUTH_UNAVAILABLE = "auth_unavailable"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    CONFIG_ERROR = "config_error"
    FAILED = "failed"


EXIT_TYPES: tuple[str, ...] = tuple((e.value for e in ExitType))

PARKING_EXITS: frozenset[str] = frozenset(
    {ExitType.AUTH_UNAVAILABLE.value, ExitType.TRANSPORT_UNAVAILABLE.value}
)

IMMEDIATE_PAUSE_EXITS: frozenset[str] = frozenset({ExitType.CONFIG_ERROR.value})

PARK_REASONS: dict[str, str] = {
    ExitType.AUTH_UNAVAILABLE.value: "a credential this trigger needs is missing or expired",
    ExitType.TRANSPORT_UNAVAILABLE.value: "the service this trigger calls was unreachable",
}

_TRANSPORT_EXC_NAMES: frozenset[str] = frozenset(
    {
        "ClientConnectorError",
        "ClientOSError",
        "ConnectError",
        "ConnectionError",
        "ConnectionResetError",
        "EndpointConnectionError",
        "ReadTimeout",
        "ServerDisconnectedError",
        "TimeoutError",
        "TooManyRedirects",
    }
)

_AUTH_HINTS: tuple[str, ...] = (
    "401",
    "403",
    "access denied",
    "credential",
    "expired token",
    "forbidden",
    "invalid api key",
    "not authenticated",
    "not authorized",
    "unauthorized",
)

_CONFIG_HINTS: tuple[str, ...] = (
    "invalid configuration",
    "missing required",
    "no such provider",
    "unknown action provider",
    "unsupported kind",
)

TRIGGER_REF = "trigger"


@dataclass
class PauseDecision:
    state: str
    consecutive_failures: int
    health: str
    reason: str = ""
    retry_after: float = 0.0

    @property
    def fires_automatically(self) -> bool:
        return TriggerState.ACTIVE.value == self.state

    def to_dict(self) -> dict[str, Any]:
        return dict(
            state=self.state,
            consecutive_failures=self.consecutive_failures,
            health_status=self.health,
            reason=self.reason,
            retry_after=self.retry_after,
        )


@dataclass(frozen=True)
class RunExit:
    kind: str

    def outcome(self) -> str:
        if self.kind in (ExitType.OK.value, ExitType.PARTIAL.value):
            return Outcome.RAN.value
        return (
            Outcome.DEFERRED.value
            if self.kind in PARKING_EXITS
            else Outcome.FAILED.value
        )

    def transition(self, failures: int, instant: float, budget: int) -> PauseDecision:
        if self.kind in (ExitType.OK.value, ExitType.PARTIAL.value):
            return PauseDecision(TriggerState.ACTIVE.value, 0, TriggerHealth.OK.value)
        if self.kind in PARKING_EXITS:
            return PauseDecision(
                TriggerState.PARKED.value,
                failures,
                TriggerHealth.PARKED.value,
                PARK_REASONS.get(
                    self.kind, "a resource this trigger needs was unavailable"
                ),
                instant + PARK_COOLDOWN_SECS,
            )
        count = failures + 1
        if self.kind in IMMEDIATE_PAUSE_EXITS:
            return PauseDecision(
                TriggerState.AUTOPAUSED.value,
                count,
                TriggerHealth.FAILING.value,
                "this trigger is misconfigured and cannot succeed on retry; paused immediately "
                "rather than after 5 identical failures",
            )
        limit = max(1, budget)
        stopped = count >= limit
        return PauseDecision(
            TriggerState.AUTOPAUSED.value if stopped else TriggerState.ACTIVE.value,
            count,
            TriggerHealth.FAILING.value if stopped else TriggerHealth.DEGRADED.value,
            (
                f"paused after {count} consecutive failures"
                if stopped
                else f"failure {count} of {limit}"
            ),
        )


@dataclass(frozen=True)
class RecordedRun:
    outcome: str
    status: str

    @classmethod
    def read(cls, row: dict[str, Any]) -> RecordedRun:
        return cls(
            str(row.get("trigger") or row.get("outcome") or ""),
            str(row.get("status") or ""),
        )

    @property
    def recovered(self) -> bool:
        if self.outcome:
            return self.outcome in {
                ExitType.OK.value,
                Outcome.RAN.value,
                Outcome.RAN_LATE.value,
            }
        return self.status in {"success", "ok"}

    @property
    def failed(self) -> bool:
        if not self.outcome:
            return self.status in {"failure", "timeout", "error"}
        return self.outcome not in PARKING_EXITS and (
            counts_toward_autopause(self.outcome)
            or self.outcome == ExitType.FAILED.value
        )


def outcome_for_exit(exit_type: str) -> str:
    return RunExit(exit_type).outcome()


def classify_exception(exc: BaseException | None) -> str:
    if exc is None:
        return ExitType.FAILED.value
    name = type(exc).__name__
    message = f"{name}: {exc}".lower()
    rules = (
        (
            ExitType.AUTH_UNAVAILABLE.value,
            lambda: any(part in message for part in _AUTH_HINTS),
        ),
        (ExitType.TRANSPORT_UNAVAILABLE.value, lambda: name in _TRANSPORT_EXC_NAMES),
        (
            ExitType.CONFIG_ERROR.value,
            lambda: any(part in message for part in _CONFIG_HINTS),
        ),
    )
    return next(
        (result for result, matches in rules if matches()), ExitType.FAILED.value
    )


def budget_for(trigger: Any) -> int:
    policy = getattr(trigger, "failure_policy", None)
    if isinstance(policy, dict):
        try:
            amount = int(policy.get("autopause_after", 0) or 0)
        except (TypeError, ValueError):
            pass
        else:
            if amount > 0:
                return amount
    return FAILURE_BUDGET


def counts_toward_autopause(outcome: str) -> bool:
    return outcome in TRUE_FAILURE_OUTCOMES


def evaluate(
    *,
    exit_type: str,
    consecutive_failures: int,
    now: float = 0.0,
    budget: int = FAILURE_BUDGET,
    quarantined: bool = False,
) -> PauseDecision:
    if exit_type and exit_type not in EXIT_TYPES:
        logging.getLogger(__name__).warning(
            "unknown trigger exit type %r — treating as a true failure; expected one of %s",
            exit_type,
            ", ".join(EXIT_TYPES),
        )
    if quarantined:
        return PauseDecision(
            TriggerState.QUARANTINED.value,
            consecutive_failures,
            TriggerHealth.FAILING.value,
            "a fire's payload matched an injection pattern; quarantined runs never auto-retry",
        )
    return RunExit(exit_type).transition(consecutive_failures, now, budget)


def consecutive_failures_from(runs: list[dict[str, Any]]) -> int:
    failures = 0
    for record in map(RecordedRun.read, runs):
        if record.recovered:
            break
        failures += int(record.failed)
    return failures


def unpark_due(*, retry_after: float, now: float) -> bool:
    return retry_after <= 0 or now >= retry_after


_RESUME_REFUSALS = {
    TriggerState.QUARANTINED.value: (
        "a quarantined trigger cannot be resumed from here — review the matched payload and "
        "re-author the trigger"
    ),
    TriggerState.RETIRED.value: "a retired trigger cannot be resumed; duplicate it instead",
}
_ATTENTION_PRESENTATION = {
    TriggerState.QUARANTINED.value: ("was quarantined", ("review", "delete")),
    TriggerState.AUTOPAUSED.value: ("paused itself", ("resume", "edit", "delete")),
}


def resume_state(state: str) -> tuple[str, str]:
    for refused, reason in _RESUME_REFUSALS.items():
        if refused == state:
            return state, reason
    return TriggerState.ACTIVE.value, ""


def needs_attention(state: str) -> bool:
    return state in _ATTENTION_PRESENTATION


def inbox_fingerprint(trigger_id: str, state: str) -> str:
    return ":".join(("trigger-attention", trigger_id, state))


@dataclass
class AttentionCard:
    trigger_id: str
    trigger_name: str
    state: str
    title: str
    body: str
    fingerprint: str
    actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in (
                "trigger_id",
                "trigger_name",
                "state",
                "title",
                "body",
                "fingerprint",
            )
        }
        result["actions"] = list(self.actions)
        return result


def attention_card(
    *, trigger_id: str, trigger_name: str, decision: PauseDecision, last_error: str = ""
) -> AttentionCard | None:
    if not needs_attention(decision.state):
        return None
    suffix, actions = _ATTENTION_PRESENTATION[decision.state]
    message = decision.reason or f"the trigger entered {decision.state}"
    detail = f". Last error: {last_error}" if last_error else ""
    return AttentionCard(
        trigger_id,
        trigger_name,
        decision.state,
        f"{trigger_name or trigger_id} {suffix}",
        message + detail,
        inbox_fingerprint(trigger_id, decision.state),
        actions,
    )


def is_duplicate_card(fingerprint: str, existing: set[str] | frozenset[str]) -> bool:
    return fingerprint in existing
