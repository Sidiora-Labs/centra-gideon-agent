"""Budget reconciliation and Gideon usage-ledger integration for Hypermid work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from gideon.hypermid.budgets import ActualUsage
from gideon.operations.usage_ledger import EventAccounting, UsageJournal

UsageOutcome = Literal["no_call", "measured", "partial", "unknown"]


class BudgetLedgerProtocol(Protocol):
    def reserve(self, request: object) -> object: ...

    def reconcile(
        self,
        reservation_id: str,
        actual: ActualUsage | None,
        provider_request_id: str | None,
        charge_unknown: bool,
    ) -> object: ...

    def release(self, reservation_id: str) -> object: ...


@dataclass(frozen=True, slots=True)
class UsageReservation:
    reservation_id: str
    request: object

    def __post_init__(self) -> None:
        if not self.reservation_id or len(self.reservation_id) > 160:
            raise ValueError("usage reservation id is invalid")


@dataclass(frozen=True, slots=True)
class UsageReconciliationEvent:
    reservation_id: str
    owner_id: str
    project_id: str
    job_class: str
    provider: str
    model: str
    outcome: UsageOutcome
    actual: ActualUsage | None
    provider_request_id: str | None
    ledger_recorded: bool

    def to_wire(self) -> dict[str, object]:
        return {
            "reservation_id": self.reservation_id,
            "owner_id": self.owner_id,
            "project_id": self.project_id,
            "job_class": self.job_class,
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "actual": (
                {
                    "input_tokens": self.actual.input_tokens,
                    "output_tokens": self.actual.output_tokens,
                    "cost_nanodollars": self.actual.cost_nanodollars,
                    "requests": self.actual.requests,
                    "wall_ms": self.actual.wall_ms,
                }
                if self.actual is not None
                else None
            ),
            "provider_request_id": self.provider_request_id,
            "ledger_recorded": self.ledger_recorded,
        }


class UsageAccountingConsumer:
    def __init__(
        self, budget_ledger: BudgetLedgerProtocol, usage_journal: UsageJournal
    ):
        self._budgets = budget_ledger
        self._journal = usage_journal

    def reserve(self, request: object) -> UsageReservation:
        reservation = self._budgets.reserve(request)
        reservation_id = getattr(reservation, "reservation_id", None)
        if not isinstance(reservation_id, str):
            reservation_id = getattr(reservation, "id", None)
        if not isinstance(reservation_id, str):
            raise TypeError("budget reservation must expose reservation_id")
        return UsageReservation(reservation_id, request)

    def no_call(self, reservation: UsageReservation) -> UsageReconciliationEvent:
        self._budgets.release(reservation.reservation_id)
        return self._event(reservation, "no_call", None, None, False)

    def unknown(
        self,
        reservation: UsageReservation,
        *,
        provider_request_id: str | None = None,
        ledger_recorded: bool = False,
    ) -> UsageReconciliationEvent:
        self._budgets.reconcile(
            reservation.reservation_id,
            None,
            provider_request_id,
            True,
        )
        return self._event(
            reservation,
            "unknown",
            None,
            provider_request_id,
            ledger_recorded,
        )

    def reconcile_summary(
        self,
        reservation: UsageReservation,
        usage: object | None,
        *,
        provider_request_id: str | None = None,
        ledger_recorded: bool = True,
    ) -> UsageReconciliationEvent:
        if usage is None:
            return self.unknown(
                reservation,
                provider_request_id=provider_request_id,
                ledger_recorded=ledger_recorded,
            )
        actual = ActualUsage(
            input_tokens=_optional_count(getattr(usage, "input_tokens", None)),
            output_tokens=_optional_count(getattr(usage, "output_tokens", None)),
            cost_nanodollars=None,
            requests=1,
            wall_ms=_count(getattr(usage, "duration_ms", 0)),
        )
        partial = _has_unknown(actual)
        self._budgets.reconcile(
            reservation.reservation_id,
            actual,
            provider_request_id,
            partial,
        )
        return self._event(
            reservation,
            "partial" if partial else "measured",
            actual,
            provider_request_id,
            ledger_recorded,
            provider=str(getattr(usage, "provider", "") or ""),
            model=str(getattr(usage, "model", "") or ""),
        )

    def reconcile_terminal_event(
        self,
        reservation: UsageReservation,
        terminal: object,
        *,
        source: str,
        session_key: str,
        agent: str,
        provider: str,
        model: str,
        provider_request_id: str | None = None,
        ledger_recorded: bool = False,
    ) -> UsageReconciliationEvent:
        served = str(getattr(terminal, "served_model_ref", "") or "")
        if ":" in served:
            served_provider, served_model = served.split(":", 1)
            if served_provider and served_model:
                provider, model = served_provider, served_model
        elif served:
            model = served
        actual = ActualUsage(
            input_tokens=_optional_count(getattr(terminal, "input_tokens", None)),
            output_tokens=_optional_count(getattr(terminal, "output_tokens", None)),
            cost_nanodollars=_event_cost_nanodollars(terminal),
            requests=1,
            wall_ms=_count(getattr(terminal, "duration_ms", 0)),
        )
        partial = _has_unknown(actual)
        self._budgets.reconcile(
            reservation.reservation_id,
            actual,
            provider_request_id,
            partial,
        )
        if not ledger_recorded:
            row = EventAccounting(terminal, model, False).record(
                source, session_key, agent, provider
            )
            row.credential_ref = None
            row.subscription_source = None
            self._journal.append(row)
            ledger_recorded = True
        return self._event(
            reservation,
            "partial" if partial else "measured",
            actual,
            provider_request_id,
            ledger_recorded,
            provider=provider,
            model=model,
        )

    @staticmethod
    def _event(
        reservation: UsageReservation,
        outcome: UsageOutcome,
        actual: ActualUsage | None,
        provider_request_id: str | None,
        ledger_recorded: bool,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> UsageReconciliationEvent:
        request = reservation.request
        return UsageReconciliationEvent(
            reservation_id=reservation.reservation_id,
            owner_id=str(getattr(request, "owner_id", "") or ""),
            project_id=str(getattr(request, "project_id", "") or ""),
            job_class=str(getattr(request, "job_class", "") or ""),
            provider=(
                provider
                if provider is not None
                else str(getattr(request, "provider_id", "") or "")
            ),
            model=(
                model
                if model is not None
                else str(getattr(request, "model_id", "") or "")
            ),
            outcome=outcome,
            actual=actual,
            provider_request_id=provider_request_id,
            ledger_recorded=ledger_recorded,
        )


def _optional_count(value: object) -> int | None:
    if value is None:
        return None
    return _count(value)


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("usage count must be a non-negative integer")
    return value


def _has_unknown(actual: ActualUsage) -> bool:
    return any(
        value is None
        for value in (
            actual.input_tokens,
            actual.output_tokens,
            actual.cost_nanodollars,
            actual.wall_ms,
        )
    )


def _event_cost_nanodollars(event: object) -> int | None:
    metadata = getattr(event, "tool_meta", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    reported = metadata.get("usage_reported")
    raw_cost = getattr(event, "cost_usd", None)
    if (
        reported is not True
        or isinstance(raw_cost, bool)
        or not isinstance(raw_cost, (int, float))
    ):
        return None
    from decimal import ROUND_HALF_EVEN, Decimal

    value = Decimal(str(raw_cost))
    if not value.is_finite() or value < 0:
        raise ValueError("provider-reported cost must be finite and non-negative")
    return int(
        (value * Decimal(1_000_000_000)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    )


__all__ = [
    "BudgetLedgerProtocol",
    "UsageAccountingConsumer",
    "UsageReconciliationEvent",
    "UsageReservation",
]
