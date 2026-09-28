"""Measure model usage once and carry it onto every terminal step-attempt row."""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from gideon.integrations.llm.events import EVENT_COMPLETE


@dataclass
class CallLog:
    """Per-dispatch observations emitted by the real guarded model stream."""

    last_activity: float = 0.0
    models: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    tokens: int = 0
    cost_usd: float = 0.0
    tokens_reported: bool = False
    tokens_missing: bool = False
    cost_reported: bool = False
    cost_missing: bool = False
    floor_tokens: int = 0
    floor_cost_usd: float = 0.0
    floor_tokens_reported: bool = False
    floor_cost_reported: bool = False
    cut_off: int = 0
    attempts: int = 0
    calls: set[str] = field(default_factory=set)
    accepting: bool = True

    def begin(self, call_id: str, provider: str, model: str) -> None:
        if not self.accepting:
            return
        self.attempts += 1
        self.calls.add(call_id)
        _append_once(self.providers, provider)
        _append_once(self.models, model)

    def event(
        self, call_id: str, event: Any, *, cost_usd: float | None = None,
        cost_reported: bool | None = None,
    ) -> bool:
        if not self.accepting:
            return False
        self.last_activity = time.time()
        if getattr(event, "kind", "") != EVENT_COMPLETE:
            return False
        reported = bool(getattr(event, "tool_meta", {}).get("usage_reported", False))
        tokens = int(getattr(event, "input_tokens", 0) or 0) + int(
            getattr(event, "output_tokens", 0) or 0
        )
        cost = float(getattr(event, "cost_usd", 0.0) or 0.0) if cost_usd is None else float(cost_usd)
        has_cost = (cost > 0.0) if cost_reported is None else cost_reported
        tokens_known = reported or tokens != 0
        cost_known = reported or has_cost
        if tokens_known:
            self.floor_tokens += tokens
            self.tokens += tokens
            self.tokens_reported = True
            self.floor_tokens_reported = True
        else:
            self.tokens_missing = True
        if cost_known:
            self.floor_cost_usd += cost
            self.cost_usd += cost
            self.cost_reported = True
            self.floor_cost_reported = True
        else:
            self.cost_missing = True
        self.calls.discard(call_id)
        return True

    def end(self, call_id: str, *, completed: bool) -> None:
        if not self.accepting:
            return
        if call_id in self.calls:
            self.calls.discard(call_id)
            if not completed:
                self.cut_off += 1

    def close(self) -> None:
        self.accepting = False


_ACTIVE: contextvars.ContextVar[CallLog | None] = contextvars.ContextVar(
    "workflow_step_call_log", default=None
)


@contextmanager
def bind_calls(log: CallLog) -> Iterator[None]:
    token = _ACTIVE.set(log)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def guarded_call_started(call_id: str, provider: str, model: str) -> None:
    log = _ACTIVE.get()
    if log is not None:
        log.begin(call_id, provider, model)


def guarded_call_event(
    call_id: str, event: Any, *, cost_usd: float | None = None,
    cost_reported: bool | None = None,
) -> bool:
    log = _ACTIVE.get()
    return log.event(call_id, event, cost_usd=cost_usd, cost_reported=cost_reported) if log is not None else False


def guarded_call_ended(call_id: str, *, completed: bool) -> None:
    log = _ACTIVE.get()
    if log is not None:
        log.end(call_id, completed=completed)


@dataclass(frozen=True)
class StepUsage:
    tokens: int | None
    cost_usd: float | None
    model: str = ""
    provider: str = ""
    calls_cut_off: int | None = 0
    substitutions: tuple[str, ...] = ()

    def fields(self) -> dict[str, Any]:
        return {
            "model_calls_open": (
                None if self.calls_cut_off is None else int(self.calls_cut_off)
            ),
            "tokens": None if self.tokens is None else int(self.tokens),
            "model": self.model,
            "provider": self.provider,
            "cost_usd": None if self.cost_usd is None else round(float(self.cost_usd), 6),
            **({"model_substituted": list(self.substitutions)} if self.substitutions else {}),
        }

    def billable(self, estimate: int = 0) -> int:
        return int(self.tokens) if self.tokens is not None else int(estimate)


NOT_RECORDED = StepUsage(tokens=None, cost_usd=None, calls_cut_off=None)
NOTHING_SENT = StepUsage(tokens=0, cost_usd=0.0)


def measured(
    calls: CallLog,
    *,
    estimate: int = 0,
    cost_estimate: float | None = None,
    model: str = "",
    provider: str = "",
) -> StepUsage:
    if calls.attempts == 0:
        return StepUsage(
            tokens=int(estimate),
            cost_usd=cost_estimate if cost_estimate is not None else 0.0,
            model=model,
            provider=provider,
        )
    open_calls = calls.cut_off + len(calls.calls)
    tokens = calls.floor_tokens if open_calls else calls.tokens
    cost = calls.floor_cost_usd if open_calls else calls.cost_usd
    tokens_reported = (
        calls.floor_tokens_reported if open_calls else calls.tokens_reported
    ) and not calls.tokens_missing
    cost_reported = (
        calls.floor_cost_reported if open_calls else calls.cost_reported
    ) and not calls.cost_missing
    return StepUsage(
        tokens=tokens if tokens_reported else None,
        cost_usd=cost if cost_reported else None,
        model=", ".join(calls.models) or model,
        provider=", ".join(calls.providers) or provider,
        calls_cut_off=open_calls,
    )


def subagent_usage(info: Any) -> StepUsage:
    served_ref = str(getattr(info, "served_model_ref", "") or "")
    provider, model = served_ref.split(":", 1) if ":" in served_ref else ("", "")
    substitutions = []
    for record in getattr(info, "model_substitutions", []) or []:
        if not isinstance(record, dict):
            continue
        requested = str(record.get("requested", "") or record.get("requested_model", "") or "")
        served = str(record.get("served", "") or record.get("served_model", "") or "")
        reason = str(record.get("why", "") or record.get("reason", "") or "")
        if requested or served:
            substitutions.append(f"{requested} → {served}" + (f": {reason}" if reason else ""))
    return StepUsage(
        tokens=int(getattr(info, "input_tokens", 0) or 0)
        + int(getattr(info, "output_tokens", 0) or 0),
        cost_usd=float(getattr(info, "cost_usd", 0.0) or 0.0),
        model=model or str(getattr(info, "model", "") or ""),
        provider=provider,
        substitutions=tuple(substitutions),
    )


def _append_once(items: list[str], value: str) -> None:
    value = str(value or "")
    if value and value not in items:
        items.append(value)
