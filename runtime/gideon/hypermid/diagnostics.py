from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Mapping

from .foundation import Digest
from .models import Cursor, JsonValue, Scope, Trace

UsageState = Literal["no_call", "missing", "reported_zero", "reported"]
EvidenceState = Literal["available", "unavailable", "unsupported"]
EngineDisposition = Literal["active", "degraded", "parked"]

MAX_OUTCOMES = 256
MAX_IDENTITIES = 100_000
MAX_TOKENS = 1_000_000_000
_REASON = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SECRET_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key",
    "api-key",
    "password",
    "secret",
    "token=",
)


def _reason(value: str) -> str:
    if not isinstance(value, str) or _REASON.fullmatch(value) is None:
        raise ValueError("diagnostic reason code is invalid")
    return value


def _digest(value: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("diagnostic digest is invalid")
    return value


def _count(value: int, name: str, *, maximum: int = MAX_TOKENS) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{name} is outside the supported range")
    return value


def _redacted_message(value: object) -> str:
    if not isinstance(value, str):
        return "diagnostic detail unavailable"
    cleaned = "".join(
        character for character in value if character == " " or character.isprintable()
    )[:2048]
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "diagnostic detail redacted"
    return cleaned


@dataclass(frozen=True, slots=True)
class UsageAccounting:
    state: UsageState
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )
        for value in values:
            if value is not None:
                _count(value, "usage token count")
        if self.state in ("no_call", "missing") and any(value is not None for value in values):
            raise ValueError("no-call and missing usage cannot contain token values")
        if self.state == "reported_zero" and values != (0, 0, 0, 0):
            raise ValueError("reported-zero usage requires explicit zero values")
        if self.state == "reported" and (any(value is None for value in values) or not any(values)):
            raise ValueError("reported usage requires complete nonzero accounting")
        if self.state not in ("no_call", "missing", "reported_zero", "reported"):
            raise ValueError("usage state is invalid")

    @classmethod
    def no_call(cls) -> UsageAccounting:
        return cls("no_call", None, None, None, None)

    @classmethod
    def missing(cls) -> UsageAccounting:
        return cls("missing", None, None, None, None)

    @classmethod
    def reported_zero(cls) -> UsageAccounting:
        return cls("reported_zero", 0, 0, 0, 0)

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "state": self.state,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass(frozen=True, slots=True)
class RegionDiagnostics:
    kind: Literal["baseline", "delta", "tail"]
    digest: str
    item_ids: tuple[str, ...] = ()
    summary_ids: tuple[str, ...] = ()
    token_mass: int = 0

    def __post_init__(self) -> None:
        if self.kind not in ("baseline", "delta", "tail"):
            raise ValueError("region kind is invalid")
        _digest(self.digest)
        _count(self.token_mass, "region token mass")
        if len(self.item_ids) > MAX_IDENTITIES or len(self.summary_ids) > MAX_IDENTITIES:
            raise ValueError("region identity list exceeds its bound")

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "kind": self.kind,
            "digest": self.digest,
            "item_ids": list(self.item_ids),
            "token_mass": self.token_mass,
        }
        if self.summary_ids:
            result["summary_ids"] = list(self.summary_ids)
        return result


@dataclass(frozen=True, slots=True)
class ModelBudgetDiagnostics:
    context_window_tokens: int
    reserved_output_tokens: int
    max_input_tokens: int
    max_items: int
    max_images: int
    baseline_tokens: int
    delta_tokens: int
    tail_tokens: int
    confidence: Literal["measured", "calibrated", "conservative"]

    def __post_init__(self) -> None:
        for name in (
            "context_window_tokens",
            "reserved_output_tokens",
            "max_input_tokens",
            "baseline_tokens",
            "delta_tokens",
            "tail_tokens",
        ):
            _count(getattr(self, name), name)
        _count(self.max_items, "max_items", maximum=1_000_000)
        _count(self.max_images, "max_images", maximum=4096)
        if self.context_window_tokens == 0 or self.max_items == 0:
            raise ValueError("model budget requires a nonzero window and item bound")
        if self.reserved_output_tokens + self.max_input_tokens > self.context_window_tokens:
            raise ValueError("input and output reservations exceed the context window")
        if self.baseline_tokens + self.delta_tokens + self.tail_tokens > self.max_input_tokens:
            raise ValueError("region token mass exceeds the input budget")
        if self.confidence not in ("measured", "calibrated", "conservative"):
            raise ValueError("budget confidence is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "context_window_tokens": self.context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "max_input_tokens": self.max_input_tokens,
            "max_items": self.max_items,
            "max_images": self.max_images,
            "baseline_tokens": self.baseline_tokens,
            "delta_tokens": self.delta_tokens,
            "tail_tokens": self.tail_tokens,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticOutcome:
    at: str
    reason_code: str
    trace: Trace
    message: str

    def __post_init__(self) -> None:
        if not self.at or len(self.at) > 64:
            raise ValueError("diagnostic timestamp is invalid")
        _reason(self.reason_code)
        object.__setattr__(self, "message", _redacted_message(self.message))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "at": self.at,
            "reason_code": self.reason_code,
            "trace": self.trace.to_wire(),
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ContextDiagnostics:
    scope: Scope
    session_id: str
    cursor: Cursor
    generation: int
    mode: Literal["off", "pass_through", "shadow", "primary"]
    provider_profile_digest: str
    journal_items: int
    journal_bytes: int
    model_budget: ModelBudgetDiagnostics
    pressure_band: Literal["normal", "advisory", "action", "emergency", "hard_wall"]
    baseline: RegionDiagnostics
    delta: RegionDiagnostics
    tail: RegionDiagnostics
    pending_reductions: int
    summary_jobs: int
    last_known_good_eligible: bool
    last_reason_code: str
    foreground_usage: UsageAccounting
    summary_usage: UsageAccounting
    subagent_usage: UsageAccounting
    generated_at: str
    render_mode: Literal["host_serialized"] = "host_serialized"
    overflow_policy: Literal["reclaim_then_refuse", "refuse_immediately"] = "reclaim_then_refuse"
    refusal_policy: Literal["refuse", "compatible_last_known_good", "host_passthrough"] = "refuse"
    policy_revision: int = 1
    protected_item_ids: tuple[str, ...] = ()
    selected_tiers: Mapping[str, int] = field(default_factory=dict)
    writer_lease: Mapping[str, JsonValue] | None = None
    recent_outcomes: tuple[DiagnosticOutcome, ...] = ()

    def __post_init__(self) -> None:
        _count(self.generation, "generation", maximum=9_007_199_254_740_991)
        _count(self.policy_revision, "policy_revision", maximum=9_007_199_254_740_991)
        if self.generation == 0 or self.policy_revision == 0:
            raise ValueError("generation and policy revision must be positive")
        _digest(self.provider_profile_digest)
        _count(self.journal_items, "journal_items", maximum=9_007_199_254_740_991)
        _count(self.journal_bytes, "journal_bytes", maximum=9_007_199_254_740_991)
        _count(self.pending_reductions, "pending_reductions", maximum=1_000_000)
        _count(self.summary_jobs, "summary_jobs", maximum=1_000_000)
        if self.mode not in ("off", "pass_through", "shadow", "primary"):
            raise ValueError("context mode is invalid")
        if self.pressure_band not in ("normal", "advisory", "action", "emergency", "hard_wall"):
            raise ValueError("pressure band is invalid")
        if (self.baseline.kind, self.delta.kind, self.tail.kind) != ("baseline", "delta", "tail"):
            raise ValueError("diagnostic regions are not ordered baseline/delta/tail")
        if len(self.protected_item_ids) > MAX_IDENTITIES or len(self.selected_tiers) > MAX_IDENTITIES:
            raise ValueError("diagnostic identity collection exceeds its bound")
        if any(isinstance(tier, bool) or not isinstance(tier, int) or tier not in range(4) for tier in self.selected_tiers.values()):
            raise ValueError("selected tier is invalid")
        if len(self.recent_outcomes) > MAX_OUTCOMES:
            raise ValueError("recent diagnostic outcomes exceed their bound")
        _reason(self.last_reason_code)
        if not self.generated_at or len(self.generated_at) > 64:
            raise ValueError("generated_at is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "scope": self.scope.to_wire(),
            "session_id": self.session_id,
            "cursor": self.cursor.to_wire(),
            "generation": self.generation,
            "mode": self.mode,
            "render_mode": self.render_mode,
            "overflow_policy": self.overflow_policy,
            "refusal_policy": self.refusal_policy,
            "policy_revision": self.policy_revision,
            "provider_profile_digest": self.provider_profile_digest,
            "journal_items": self.journal_items,
            "journal_bytes": self.journal_bytes,
            "model_budget": self.model_budget.to_wire(),
            "pressure_band": self.pressure_band,
            "baseline": self.baseline.to_wire(),
            "delta": self.delta.to_wire(),
            "tail": self.tail.to_wire(),
            "pending_reductions": self.pending_reductions,
            "summary_jobs": self.summary_jobs,
            "last_known_good_eligible": self.last_known_good_eligible,
            "last_reason_code": self.last_reason_code,
            "foreground_usage": self.foreground_usage.to_wire(),
            "summary_usage": self.summary_usage.to_wire(),
            "subagent_usage": self.subagent_usage.to_wire(),
            "recent_outcomes": [outcome.to_wire() for outcome in self.recent_outcomes],
            "generated_at": self.generated_at,
        }
        if self.protected_item_ids:
            result["protected_item_ids"] = list(self.protected_item_ids)
        if self.selected_tiers:
            result["selected_tiers"] = dict(self.selected_tiers)
        if self.writer_lease is not None:
            result["writer_lease"] = dict(self.writer_lease)
        return result


@dataclass(frozen=True, slots=True)
class DiagnosticFailure:
    reason_code: str
    message: str
    retryable: bool
    failed_at_ms: int

    def __post_init__(self) -> None:
        _reason(self.reason_code)
        _count(self.failed_at_ms, "failed_at_ms", maximum=9_007_199_254_740_991)
        object.__setattr__(self, "message", _redacted_message(self.message))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "reason_code": self.reason_code,
            "message": self.message,
            "retryable": self.retryable,
            "failed_at_ms": self.failed_at_ms,
        }


@dataclass(frozen=True, slots=True)
class ContextDiagnosticsSnapshot:
    state: EvidenceState
    observed_at_ms: int
    disposition: EngineDisposition
    value: ContextDiagnostics | None = None
    current_error: DiagnosticFailure | None = None
    last_good: ContextDiagnostics | None = None

    def __post_init__(self) -> None:
        _count(self.observed_at_ms, "observed_at_ms", maximum=9_007_199_254_740_991)
        if self.state == "available":
            if self.disposition != "active" or self.value is None or self.current_error is not None:
                raise ValueError("available diagnostics require a current successful value")
        elif self.state == "unavailable":
            if self.disposition not in ("degraded", "parked") or self.value is not None or self.current_error is None:
                raise ValueError("unavailable diagnostics require a current error")
        elif self.state == "unsupported":
            if self.value is not None or self.current_error is not None or self.last_good is not None:
                raise ValueError("unsupported diagnostics cannot claim evidence")
        else:
            raise ValueError("diagnostic evidence state is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "state": self.state,
            "observed_at_ms": self.observed_at_ms,
            "disposition": self.disposition,
        }
        if self.value is not None:
            result["value"] = self.value.to_wire()
        if self.current_error is not None:
            result["current_error"] = self.current_error.to_wire()
        if self.last_good is not None:
            result["last_good"] = self.last_good.to_wire()
        return result


class DiagnosticStore:
    def __init__(self, *, retention: int = 64, observed_at_ms: int = 0) -> None:
        if isinstance(retention, bool) or not isinstance(retention, int) or not 1 <= retention <= 256:
            raise ValueError("diagnostic retention must be between 1 and 256")
        self._lock = threading.Lock()
        self._retained: deque[ContextDiagnosticsSnapshot] = deque(maxlen=retention)
        self._latest = ContextDiagnosticsSnapshot(
            state="unsupported", observed_at_ms=observed_at_ms, disposition="parked"
        )

    def record_good(self, value: ContextDiagnostics, *, observed_at_ms: int) -> ContextDiagnosticsSnapshot:
        snapshot = ContextDiagnosticsSnapshot(
            state="available",
            observed_at_ms=observed_at_ms,
            disposition="active",
            value=value,
            last_good=value,
        )
        return self._record(snapshot)

    def record_failure(
        self,
        failure: DiagnosticFailure,
        *,
        observed_at_ms: int,
        parked: bool = False,
    ) -> ContextDiagnosticsSnapshot:
        with self._lock:
            last_good = self._latest.last_good
        snapshot = ContextDiagnosticsSnapshot(
            state="unavailable",
            observed_at_ms=observed_at_ms,
            disposition="parked" if parked else "degraded",
            current_error=failure,
            last_good=last_good,
        )
        return self._record(snapshot)

    def snapshot(self) -> ContextDiagnosticsSnapshot:
        with self._lock:
            return self._latest

    def retained(self) -> tuple[ContextDiagnosticsSnapshot, ...]:
        with self._lock:
            return tuple(self._retained)

    def _record(self, snapshot: ContextDiagnosticsSnapshot) -> ContextDiagnosticsSnapshot:
        with self._lock:
            self._retained.append(snapshot)
            self._latest = snapshot
        return snapshot


@dataclass(frozen=True, slots=True)
class MemoryRecoveryDiagnostics:
    schema_version: int
    compatibility_floor: int
    schema_digest: Digest
    scope: Scope
    cursor: Cursor
    authoritative_digest: Digest
    record_count: int
    revision_count: int
    source_count: int
    lineage_count: int
    memory_fts_count: int
    source_fts_count: int
    embedding_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_digest", Digest(self.schema_digest))
        object.__setattr__(self, "authoritative_digest", Digest(self.authoritative_digest))
        for name in (
            "schema_version",
            "compatibility_floor",
            "record_count",
            "revision_count",
            "source_count",
            "lineage_count",
            "memory_fts_count",
            "source_fts_count",
            "embedding_count",
        ):
            _count(getattr(self, name), name, maximum=9_007_199_254_740_991)
        if self.schema_version < 1 or not 1 <= self.compatibility_floor <= self.schema_version:
            raise ValueError("memory recovery schema evidence is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "compatibility_floor": self.compatibility_floor,
            "schema_digest": str(self.schema_digest),
            "scope": self.scope.to_wire(),
            "cursor": self.cursor.to_wire(),
            "authoritative_digest": str(self.authoritative_digest),
            "record_count": self.record_count,
            "revision_count": self.revision_count,
            "source_count": self.source_count,
            "lineage_count": self.lineage_count,
            "memory_fts_count": self.memory_fts_count,
            "source_fts_count": self.source_fts_count,
            "embedding_count": self.embedding_count,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, JsonValue]) -> MemoryRecoveryDiagnostics:
        expected = {
            "schema_version",
            "compatibility_floor",
            "schema_digest",
            "scope",
            "cursor",
            "authoritative_digest",
            "record_count",
            "revision_count",
            "source_count",
            "lineage_count",
            "memory_fts_count",
            "source_fts_count",
            "embedding_count",
        }
        if set(value) != expected:
            raise ValueError("memory recovery diagnostic fields are not canonical")
        return cls(
            schema_version=value["schema_version"],
            compatibility_floor=value["compatibility_floor"],
            schema_digest=Digest(value["schema_digest"]),
            scope=Scope.from_wire(value["scope"]),
            cursor=Cursor.from_wire(value["cursor"]),
            authoritative_digest=Digest(value["authoritative_digest"]),
            record_count=value["record_count"],
            revision_count=value["revision_count"],
            source_count=value["source_count"],
            lineage_count=value["lineage_count"],
            memory_fts_count=value["memory_fts_count"],
            source_fts_count=value["source_fts_count"],
            embedding_count=value["embedding_count"],
        )
