"""Typed Hypermid diagnostics, logs, and maintenance control plane."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

from .client import HypermidClient, HypermidOutcomeUnknown
from .models import Cursor, JsonValue, Scope

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SECRET_KEY = re.compile(
    r"(?:authorization|bearer|credential|password|secret|token|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]+|(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+)"
)

TerminalState = Literal["committed", "failed", "cancelled", "outcome_unknown"]
ReceiptState = Literal[
    "running", "committed", "failed", "cancelled", "outcome_unknown"
]


class OperatorContractError(ValueError):
    """Raised when a daemon operator response violates the public contract."""


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OperatorContractError(f"{name} must be an object")
    return value


def _text(value: object, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise OperatorContractError(f"{name} must be a non-empty string")
    return value


def _digest(value: object, name: str) -> str:
    result = _text(value, name, 64)
    if _DIGEST.fullmatch(result) is None:
        raise OperatorContractError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _timestamp(value: object, name: str) -> str:
    result = _text(value, name, 80)
    try:
        datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorContractError(f"{name} must be an ISO-8601 timestamp") from exc
    return result


def _scope(value: object, expected: Scope) -> Scope:
    try:
        result = Scope.from_wire(value)
    except ValueError as exc:
        raise OperatorContractError("response scope is invalid") from exc
    if result != expected:
        raise OperatorContractError("response scope does not match the authenticated scope")
    return result


def _cursor(value: object, name: str = "cursor") -> Cursor:
    try:
        return Cursor.from_wire(value)
    except ValueError as exc:
        raise OperatorContractError(f"{name} is invalid") from exc


def _redact(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            result[key] = "[REDACTED]" if _SECRET_KEY.search(key) else _redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return str(value)


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    id: str
    title: str
    health: Literal["healthy", "degraded", "unhealthy", "unknown"]
    observed_at: str
    cached: bool
    summary: str
    next_action: str | None
    evidence_digest: str | None

    @classmethod
    def from_wire(cls, value: object) -> DiagnosticCheck:
        raw = _object(value, "diagnostic check")
        health = raw.get("health")
        if health not in ("healthy", "degraded", "unhealthy", "unknown"):
            raise OperatorContractError("diagnostic health is invalid")
        cached = raw.get("cached")
        if not isinstance(cached, bool):
            raise OperatorContractError("diagnostic cached flag must be boolean")
        next_action = raw.get("next_action")
        evidence_digest = raw.get("evidence_digest")
        return cls(
            id=_text(raw.get("id"), "diagnostic id", 160),
            title=_text(raw.get("title"), "diagnostic title", 160),
            health=health,
            observed_at=_timestamp(raw.get("observed_at"), "diagnostic observed_at"),
            cached=cached,
            summary=str(_redact(_text(raw.get("summary"), "diagnostic summary", 2048))),
            next_action=(
                str(_redact(_text(next_action, "diagnostic next_action", 1024)))
                if next_action is not None
                else None
            ),
            evidence_digest=(
                _digest(evidence_digest, "diagnostic evidence_digest")
                if evidence_digest is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    scope: Scope
    observed_at: str
    cursor: Cursor
    cached: bool
    checks: tuple[DiagnosticCheck, ...]

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> DiagnosticsSnapshot:
        raw = _object(value, "diagnostics snapshot")
        checks = raw.get("checks")
        if not isinstance(checks, list) or len(checks) > 512:
            raise OperatorContractError("diagnostics checks must be a bounded array")
        cached = raw.get("cached")
        if not isinstance(cached, bool):
            raise OperatorContractError("diagnostics cached flag must be boolean")
        return cls(
            scope=_scope(raw.get("scope"), expected_scope),
            observed_at=_timestamp(raw.get("observed_at"), "diagnostics observed_at"),
            cursor=_cursor(raw.get("cursor")),
            cached=cached,
            checks=tuple(DiagnosticCheck.from_wire(item) for item in checks),
        )


@dataclass(frozen=True, slots=True)
class LogEntry:
    cursor: Cursor
    observed_at: str
    severity: str
    component: str
    message: str
    fields: Mapping[str, JsonValue]

    @classmethod
    def from_wire(cls, value: object) -> LogEntry:
        raw = _object(value, "log entry")
        fields = _redact(_object(raw.get("fields", {}), "log fields"))
        if not isinstance(fields, Mapping):
            raise OperatorContractError("redacted log fields must be an object")
        return cls(
            cursor=_cursor(raw.get("cursor"), "log cursor"),
            observed_at=_timestamp(raw.get("observed_at"), "log observed_at"),
            severity=_text(raw.get("severity"), "log severity", 24),
            component=_text(raw.get("component"), "log component", 160),
            message=str(_redact(_text(raw.get("message"), "log message", 8192))),
            fields=fields,
        )


@dataclass(frozen=True, slots=True)
class LogPage:
    scope: Scope
    entries: tuple[LogEntry, ...]
    cursor: Cursor
    gap: bool
    recovery_cursor: Cursor | None

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> LogPage:
        raw = _object(value, "log page")
        entries = raw.get("entries")
        if not isinstance(entries, list) or len(entries) > 1_000:
            raise OperatorContractError("log entries must be a bounded array")
        gap = raw.get("gap", False)
        if not isinstance(gap, bool):
            raise OperatorContractError("log gap flag must be boolean")
        recovery = raw.get("recovery_cursor")
        if gap and recovery is None:
            raise OperatorContractError("a log retention gap requires a recovery cursor")
        return cls(
            scope=_scope(raw.get("scope"), expected_scope),
            entries=tuple(LogEntry.from_wire(item) for item in entries),
            cursor=_cursor(raw.get("cursor")),
            gap=gap,
            recovery_cursor=(
                _cursor(recovery, "recovery cursor") if recovery is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str
    title: str
    effect: str
    state: str
    detail: str | None = None

    @classmethod
    def from_wire(cls, value: object) -> PlanStep:
        raw = _object(value, "plan step")
        effect = raw.get("effect")
        if effect not in (
            "read",
            "write_derivative",
            "write_authoritative",
            "delete_derivative",
            "delete_authoritative",
            "restart",
        ):
            raise OperatorContractError("plan step effect is invalid")
        state = raw.get("state")
        if state not in (
            "planned",
            "running",
            "committed",
            "skipped",
            "failed",
            "cancelled",
            "unknown",
        ):
            raise OperatorContractError("plan step state is invalid")
        detail = raw.get("detail")
        return cls(
            id=_text(raw.get("id"), "plan step id", 160),
            title=_text(raw.get("title"), "plan step title", 240),
            effect=effect,
            state=state,
            detail=(str(_redact(_text(detail, "plan step detail", 2048))) if detail else None),
        )


@dataclass(frozen=True, slots=True)
class ActionPlan:
    plan_id: str
    operation: str
    scope: Scope
    created_at: str
    expires_at: str
    plan_digest: str
    destructive: bool
    restart_required: bool
    steps: tuple[PlanStep, ...]
    blockers: tuple[str, ...]
    authority_digest: str | None = None
    blocker_digest: str | None = None
    raw: Mapping[str, JsonValue] | None = None

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> ActionPlan:
        raw = _object(value, "action plan")
        steps = raw.get("steps")
        blockers = raw.get("blockers")
        if not isinstance(steps, list) or not steps or len(steps) > 256:
            raise OperatorContractError("plan steps must be a non-empty bounded array")
        if not isinstance(blockers, list) or len(blockers) > 128:
            raise OperatorContractError("plan blockers must be a bounded array")
        destructive = raw.get("destructive")
        restart_required = raw.get("restart_required", False)
        if not isinstance(destructive, bool) or not isinstance(restart_required, bool):
            raise OperatorContractError("plan flags must be boolean")
        authority_digest = raw.get("authority_digest")
        blocker_digest = raw.get("blocker_digest")
        return cls(
            plan_id=_text(raw.get("plan_id"), "plan id", 160),
            operation=_text(raw.get("operation"), "plan operation", 160),
            scope=_scope(raw.get("scope"), expected_scope),
            created_at=_timestamp(raw.get("created_at"), "plan created_at"),
            expires_at=_timestamp(raw.get("expires_at"), "plan expires_at"),
            plan_digest=_digest(raw.get("plan_digest"), "plan digest"),
            destructive=destructive,
            restart_required=restart_required,
            steps=tuple(PlanStep.from_wire(item) for item in steps),
            blockers=tuple(_text(item, "plan blocker", 1024) for item in blockers),
            authority_digest=(
                _digest(authority_digest, "authority digest")
                if authority_digest is not None
                else None
            ),
            blocker_digest=(
                _digest(blocker_digest, "blocker digest")
                if blocker_digest is not None
                else None
            ),
            raw=_redact(raw),
        )


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    job_id: str
    operation: str
    scope: Scope
    plan_digest: str
    state: ReceiptState
    started_at: str
    finished_at: str | None
    cursor: Cursor | None
    steps: tuple[PlanStep, ...]
    rollback_available: bool
    artifact_digest: str | None
    error: Mapping[str, JsonValue] | None

    @property
    def terminal(self) -> bool:
        return self.state != "running"

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> ActionReceipt:
        raw = _object(value, "action receipt")
        state = raw.get("state")
        if state not in (
            "running",
            "committed",
            "failed",
            "cancelled",
            "outcome_unknown",
        ):
            raise OperatorContractError("receipt state is invalid")
        steps = raw.get("steps")
        if not isinstance(steps, list) or len(steps) > 256:
            raise OperatorContractError("receipt steps must be a bounded array")
        rollback = raw.get("rollback_available", False)
        if not isinstance(rollback, bool):
            raise OperatorContractError("rollback_available must be boolean")
        finished_at = raw.get("finished_at")
        cursor = raw.get("cursor")
        artifact_digest = raw.get("artifact_digest")
        error = raw.get("error")
        if state == "committed" and error is not None:
            raise OperatorContractError("a committed receipt cannot contain an error")
        if state != "running" and finished_at is None:
            raise OperatorContractError("a terminal receipt requires finished_at")
        redacted_error = _redact(error) if error is not None else None
        if redacted_error is not None and not isinstance(redacted_error, Mapping):
            raise OperatorContractError("receipt error must be an object")
        return cls(
            job_id=_text(raw.get("job_id"), "job id", 160),
            operation=_text(raw.get("operation"), "receipt operation", 160),
            scope=_scope(raw.get("scope"), expected_scope),
            plan_digest=_digest(raw.get("plan_digest"), "receipt plan digest"),
            state=state,
            started_at=_timestamp(raw.get("started_at"), "receipt started_at"),
            finished_at=(
                _timestamp(finished_at, "receipt finished_at")
                if finished_at is not None
                else None
            ),
            cursor=_cursor(cursor) if cursor is not None else None,
            steps=tuple(PlanStep.from_wire(item) for item in steps),
            rollback_available=rollback,
            artifact_digest=(
                _digest(artifact_digest, "artifact digest")
                if artifact_digest is not None
                else None
            ),
            error=redacted_error,
        )


class HypermidOperations:
    """Operate one authenticated Scope through the real Hypermid client."""

    def __init__(self, client: HypermidClient) -> None:
        self.client = client
        self.last_diagnostics: DiagnosticsSnapshot | None = None

    async def status(self) -> Mapping[str, JsonValue]:
        value = await self.client.request("operator.status", {})
        result = _redact(_object(value, "operator status"))
        if not isinstance(result, Mapping):
            raise OperatorContractError("operator status must be an object")
        return result

    async def diagnostics(self, *, refresh: bool = False) -> DiagnosticsSnapshot:
        operation = "diagnostics.rerun" if refresh else "diagnostics.get"
        value = await self.client.request(operation, {})
        snapshot = DiagnosticsSnapshot.from_wire(value, self.client.scope)
        self.last_diagnostics = snapshot
        return snapshot

    async def logs(
        self,
        *,
        after: Cursor | None = None,
        filters: Mapping[str, JsonValue] | None = None,
        limit: int = 200,
    ) -> LogPage:
        if not 1 <= limit <= 1_000:
            raise ValueError("log limit must be between 1 and 1000")
        payload: dict[str, JsonValue] = {"limit": limit}
        if after is not None:
            payload["after"] = after.to_wire()
        if filters:
            payload["filters"] = dict(filters)
        value = await self.client.request("logs.read", payload)
        return LogPage.from_wire(value, self.client.scope)

    async def plan_maintenance(
        self, action: str, *, params: Mapping[str, JsonValue] | None = None
    ) -> ActionPlan:
        if action not in {
            "integrity_check",
            "reconcile",
            "reindex",
            "compact",
            "cleanup_stale_cache",
            "rebuild_derivatives",
        }:
            raise ValueError("unsupported maintenance action")
        value = await self.client.request(
            "maintenance.plan", {"action": action, "params": dict(params or {})}
        )
        return ActionPlan.from_wire(value, self.client.scope)

    async def apply_maintenance(
        self,
        plan: ActionPlan,
        *,
        reviewed_digest: str,
        confirm_destructive: bool = False,
    ) -> ActionReceipt:
        if plan.scope != self.client.scope:
            raise ValueError("plan scope does not match the authenticated scope")
        if reviewed_digest != plan.plan_digest:
            raise ValueError("reviewed digest does not match the current plan")
        if plan.blockers:
            raise ValueError("a blocked maintenance plan cannot be applied")
        if plan.destructive and not confirm_destructive:
            raise ValueError("destructive maintenance requires explicit confirmation")
        payload: dict[str, JsonValue] = {
            "plan_id": plan.plan_id,
            "plan_digest": plan.plan_digest,
            "confirm_destructive": confirm_destructive,
        }
        if plan.authority_digest is not None:
            payload["authority_digest"] = plan.authority_digest
        if plan.blocker_digest is not None:
            payload["blocker_digest"] = plan.blocker_digest
        try:
            value = await self.client.request(
                "maintenance.apply", payload, effect_kind="durable"
            )
        except HypermidOutcomeUnknown:
            raise
        receipt = ActionReceipt.from_wire(value, self.client.scope)
        if receipt.plan_digest != plan.plan_digest:
            raise OperatorContractError("receipt does not bind the reviewed plan")
        return receipt

    async def maintenance_status(self, job_id: str) -> ActionReceipt:
        value = await self.client.request("maintenance.status", {"job_id": job_id})
        return ActionReceipt.from_wire(value, self.client.scope)

    async def cancel_maintenance(self, job_id: str) -> ActionReceipt:
        value = await self.client.request(
            "maintenance.cancel", {"job_id": job_id}, effect_kind="idempotent"
        )
        return ActionReceipt.from_wire(value, self.client.scope)


__all__ = [
    "ActionPlan",
    "ActionReceipt",
    "DiagnosticCheck",
    "DiagnosticsSnapshot",
    "HypermidOperations",
    "LogEntry",
    "LogPage",
    "OperatorContractError",
    "PlanStep",
]
