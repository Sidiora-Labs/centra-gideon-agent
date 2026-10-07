from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, cast

from .diagnostics import MemoryRecoveryDiagnostics
from .foundation import Cursor, Digest, Error, Id, Scope, Trace
from .history import HistoryJournal, JournalRange, RecoveredItem, SourceAdapter
from .models import JsonValue

if TYPE_CHECKING:
    from .client import HypermidClient

MAX_NORMALIZED_PROJECTION_BYTES = 8 * 1024 * 1024
_BUDGET_FIELDS = (
    "context_window_tokens",
    "reserved_output_tokens",
    "max_input_tokens",
    "max_items",
    "max_images",
    "baseline_tokens",
    "delta_tokens",
    "tail_tokens",
    "confidence",
)


class RecoveryError(ValueError):
    pass


class RecoveryRefusal(RecoveryError):
    def __init__(self, error: Error) -> None:
        self.error = error
        super().__init__(error.message)


def _refusal(code: str, message: str) -> RecoveryRefusal:
    return RecoveryRefusal(Error(code=code, message=message, retryable=True))


@dataclass(frozen=True, slots=True)
class RecoveryBinding:
    scope: Scope
    session_id: Id
    cursor: Cursor
    source_digest: Digest
    generation: int
    provider_profile_digest: Digest
    policy_revision: int
    baseline_digest: Digest
    delta_digest: Digest
    tail_digest: Digest
    model_budget: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", Id(self.session_id))
        for field in (
            "source_digest",
            "provider_profile_digest",
            "baseline_digest",
            "delta_digest",
            "tail_digest",
        ):
            object.__setattr__(self, field, Digest(getattr(self, field)))
        budget = {field: self.model_budget[field] for field in _BUDGET_FIELDS}
        object.__setattr__(self, "model_budget", budget)
        self.validate()

    def validate(self) -> None:
        budget = self.model_budget
        integer_fields = _BUDGET_FIELDS[:-1]
        if (
            isinstance(self.generation, bool)
            or self.generation < 1
            or isinstance(self.policy_revision, bool)
            or self.policy_revision < 1
            or any(
                isinstance(budget[field], bool)
                or not isinstance(budget[field], int)
                or budget[field] < 0
                for field in integer_fields
            )
            or budget["context_window_tokens"] < 1
            or budget["max_items"] < 1
            or budget["reserved_output_tokens"] > budget["context_window_tokens"]
            or budget["max_input_tokens"]
            > budget["context_window_tokens"] - budget["reserved_output_tokens"]
            or budget["baseline_tokens"]
            + budget["delta_tokens"]
            + budget["tail_tokens"]
            > budget["max_input_tokens"]
            or budget["confidence"] not in {"measured", "calibrated", "conservative"}
        ):
            raise RecoveryError("last-known-good budget or binding is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_wire(),
            "session_id": str(self.session_id),
            "cursor": self.cursor.to_wire(),
            "source_digest": str(self.source_digest),
            "generation": self.generation,
            "provider_profile_digest": str(self.provider_profile_digest),
            "policy_revision": self.policy_revision,
            "baseline_digest": str(self.baseline_digest),
            "delta_digest": str(self.delta_digest),
            "tail_digest": str(self.tail_digest),
            "model_budget": dict(self.model_budget),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RecoveryBinding:
        return cls(
            scope=Scope.from_wire(value["scope"]),
            session_id=Id(value["session_id"]),
            cursor=Cursor.from_wire(value["cursor"]),
            source_digest=Digest(value["source_digest"]),
            generation=value["generation"],
            provider_profile_digest=Digest(value["provider_profile_digest"]),
            policy_revision=value["policy_revision"],
            baseline_digest=Digest(value["baseline_digest"]),
            delta_digest=Digest(value["delta_digest"]),
            tail_digest=Digest(value["tail_digest"]),
            model_budget=value["model_budget"],
        )

    @classmethod
    def from_projection(
        cls, projection: Mapping[str, Any], model_budget: Mapping[str, Any]
    ) -> RecoveryBinding:
        return cls(
            scope=Scope.from_wire(projection["scope"]),
            session_id=Id(projection["session_id"]),
            cursor=Cursor.from_wire(projection["source_cursor"]),
            source_digest=Digest(projection["source_digest"]),
            generation=projection["generation"],
            provider_profile_digest=Digest(projection["provider_profile_digest"]),
            policy_revision=projection["policy_revision"],
            baseline_digest=Digest(projection["baseline"]["digest"]),
            delta_digest=Digest(projection["delta"]["digest"]),
            tail_digest=Digest(projection["tail"]["digest"]),
            model_budget=model_budget,
        )


@dataclass(frozen=True, slots=True)
class LastKnownGood:
    projection_id: Id
    binding: RecoveryBinding
    output_digest: Digest
    normalized_digest: Digest
    normalized_projection: bytes
    stored_at: str

    @classmethod
    def capture(
        cls,
        projection: Mapping[str, Any],
        model_budget: Mapping[str, Any],
        *,
        stored_at: str,
    ) -> LastKnownGood:
        normalized_projection = _canonical(projection)
        record = cls(
            projection_id=Id(projection["projection_id"]),
            binding=RecoveryBinding.from_projection(projection, model_budget),
            output_digest=Digest(projection["output_digest"]),
            normalized_digest=Digest.sha256(normalized_projection),
            normalized_projection=normalized_projection,
            stored_at=stored_at,
        )
        record.validate()
        return record

    def validate(self) -> Mapping[str, Any]:
        self.binding.validate()
        if (
            not self.stored_at
            or not self.normalized_projection
            or len(self.normalized_projection) > MAX_NORMALIZED_PROJECTION_BYTES
            or Digest.sha256(self.normalized_projection) != self.normalized_digest
        ):
            raise RecoveryError("last-known-good record is corrupt or partial")
        try:
            projection = json.loads(self.normalized_projection)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryError("last-known-good record is corrupt or partial") from exc
        if not isinstance(projection, Mapping):
            raise RecoveryError("last-known-good projection must be an object")
        expected = RecoveryBinding.from_projection(
            projection, self.binding.model_budget
        )
        blocks = projection.get("blocks")
        if (
            expected != self.binding
            or projection.get("projection_id") != self.projection_id
            or projection.get("output_digest") != self.output_digest
            or not isinstance(blocks, list)
            or Digest.sha256(_canonical(blocks)) != self.output_digest
        ):
            raise RecoveryError("last-known-good record is corrupt or partial")
        return projection

    def to_mapping(self) -> dict[str, Any]:
        return {
            "projection_id": str(self.projection_id),
            "binding": self.binding.to_mapping(),
            "output_digest": str(self.output_digest),
            "normalized_digest": str(self.normalized_digest),
            "normalized_projection": self.normalized_projection.hex(),
            "stored_at": self.stored_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LastKnownGood:
        record = cls(
            projection_id=Id(value["projection_id"]),
            binding=RecoveryBinding.from_mapping(value["binding"]),
            output_digest=Digest(value["output_digest"]),
            normalized_digest=Digest(value["normalized_digest"]),
            normalized_projection=bytes.fromhex(value["normalized_projection"]),
            stored_at=value["stored_at"],
        )
        record.validate()
        return record


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    binding: RecoveryBinding
    input_tokens: int
    item_count: int
    image_count: int

    def validate_fit(self) -> None:
        for value in (self.input_tokens, self.item_count, self.image_count):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RecoveryError("replay usage is invalid")
        budget = self.binding.model_budget
        if (
            self.input_tokens > budget["max_input_tokens"]
            or self.item_count > budget["max_items"]
            or self.image_count > budget["max_images"]
        ):
            raise _refusal(
                "RECOVERY_BUDGET_MISMATCH",
                "last-known-good projection does not fit the active budget",
            )


@dataclass(frozen=True, slots=True)
class RebuiltState:
    scope: Scope
    session_id: Id
    cursor: Cursor
    source_digest: Digest
    items: tuple[RecoveredItem, ...]


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    content_digest: Digest
    reason: str
    quarantined_at_ms: int
    byte_length: int


class RecoveryStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.quarantine = self.root / "quarantine"
        self.quarantine.mkdir(parents=True, exist_ok=True)
        self.active = self.root / "last_known_good.json"

    def publish(self, record: LastKnownGood) -> None:
        record.validate()
        _write_atomic(self.active, _canonical(record.to_mapping()))

    def load(self) -> LastKnownGood | None:
        if not self.active.exists():
            return None
        content = self.active.read_bytes()
        try:
            raw = json.loads(content)
            if not isinstance(raw, Mapping):
                raise RecoveryError("recovery record must be an object")
            return LastKnownGood.from_mapping(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._quarantine(content, str(exc))
            return None

    def replay(self, request: ReplayRequest) -> Mapping[str, Any]:
        request.validate_fit()
        record = self.load()
        if record is None:
            raise _refusal(
                "RECOVERY_CORRUPT",
                "last-known-good projection is unavailable or corrupt",
            )
        if record.binding != request.binding:
            raise _refusal(
                "RECOVERY_BINDING_MISMATCH",
                "last-known-good projection does not match the active binding",
            )
        return record.validate()

    def quarantine_entries(self) -> tuple[QuarantineEntry, ...]:
        entries = []
        for path in sorted(self.quarantine.glob("*.meta")):
            value = json.loads(path.read_bytes())
            entries.append(
                QuarantineEntry(
                    content_digest=Digest(value["content_digest"]),
                    reason=value["reason"],
                    quarantined_at_ms=value["quarantined_at_ms"],
                    byte_length=value["byte_length"],
                )
            )
        return tuple(entries)

    def _quarantine(self, content: bytes, reason: str) -> None:
        digest = Digest.sha256(content)
        now = time.time_ns() // 1_000_000
        stem = f"{digest}-{now}"
        payload = self.quarantine / f"{stem}.bin"
        metadata = self.quarantine / f"{stem}.meta"
        os.replace(self.active, payload)
        _write_atomic(
            metadata,
            _canonical(
                {
                    "content_digest": str(digest),
                    "reason": reason[:2_048],
                    "quarantined_at_ms": now,
                    "byte_length": len(content),
                }
            ),
        )


def rebuild_from_journal(
    journal: HistoryJournal, adapter: SourceAdapter
) -> RebuiltState:
    cursor = journal.cursor
    if cursor.sequence == 0:
        return RebuiltState(
            scope=journal.scope,
            session_id=journal.session_id,
            cursor=cursor,
            source_digest=Digest.sha256(b""),
            items=(),
        )
    journal_range = JournalRange(Cursor(cursor.epoch, 1), cursor)
    items = journal.recover(adapter, cursor_range=journal_range)
    if len(items) != cursor.sequence or any(
        recovered.item.cursor.sequence != index
        or recovered.item.scope != journal.scope
        or recovered.item.session_id != journal.session_id
        or Digest.sha256(recovered.source_bytes) != recovered.item.source_digest
        for index, recovered in enumerate(items, start=1)
    ):
        raise RecoveryError("journal replay has a gap or unverified source")
    return RebuiltState(
        scope=journal.scope,
        session_id=journal.session_id,
        cursor=cursor,
        source_digest=journal.source_digest(journal_range),
        items=items,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".pending-{Digest.sha256(content)}")
    if temporary.exists():
        temporary.unlink()
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclass(frozen=True, slots=True)
class MemorySnapshotReceipt:
    scope: Scope
    cursor: Cursor
    manifest_digest: Digest
    image_digest: Digest
    byte_length: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_digest", Digest(self.manifest_digest))
        object.__setattr__(self, "image_digest", Digest(self.image_digest))
        if (
            isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or self.byte_length < 0
        ):
            raise RecoveryError("memory snapshot byte length is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_wire(),
            "cursor": self.cursor.to_wire(),
            "manifest_digest": str(self.manifest_digest),
            "image_digest": str(self.image_digest),
            "byte_length": self.byte_length,
        }

    @classmethod
    def from_mapping(cls, value: object) -> MemorySnapshotReceipt:
        raw = _strict_mapping(
            value,
            {"scope", "cursor", "manifest_digest", "image_digest", "byte_length"},
            "memory snapshot receipt",
        )
        return cls(
            Scope.from_wire(raw["scope"]),
            Cursor.from_wire(raw["cursor"]),
            Digest(raw["manifest_digest"]),
            Digest(raw["image_digest"]),
            raw["byte_length"],
        )


@dataclass(frozen=True, slots=True)
class MemoryRestoreReceipt:
    scope: Scope
    cursor: Cursor
    manifest_digest: Digest
    active_digest: Digest
    byte_length: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_digest", Digest(self.manifest_digest))
        object.__setattr__(self, "active_digest", Digest(self.active_digest))
        if (
            isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or self.byte_length < 0
        ):
            raise RecoveryError("memory restore byte length is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_wire(),
            "cursor": self.cursor.to_wire(),
            "manifest_digest": str(self.manifest_digest),
            "active_digest": str(self.active_digest),
            "byte_length": self.byte_length,
        }

    @classmethod
    def from_mapping(cls, value: object) -> MemoryRestoreReceipt:
        raw = _strict_mapping(
            value,
            {"scope", "cursor", "manifest_digest", "active_digest", "byte_length"},
            "memory restore receipt",
        )
        return cls(
            Scope.from_wire(raw["scope"]),
            Cursor.from_wire(raw["cursor"]),
            Digest(raw["manifest_digest"]),
            Digest(raw["active_digest"]),
            raw["byte_length"],
        )


class MemoryRecoveryClient:
    def __init__(self, client: HypermidClient) -> None:
        self._client = client

    async def inspect(self, *, scope: Scope, trace: Trace) -> MemoryRecoveryDiagnostics:
        value = await self._client.request(
            "memory.recovery.inspect",
            {"scope": scope.to_wire()},
            scope=scope,
            trace=trace,
            effect_kind="query",
        )
        return MemoryRecoveryDiagnostics.from_wire(cast(Mapping[str, JsonValue], value))

    async def snapshot(
        self, *, scope: Scope, artifact_id: Id, trace: Trace
    ) -> MemorySnapshotReceipt:
        value = await self._client.request(
            "memory.recovery.snapshot",
            {"scope": scope.to_wire(), "artifact_id": str(Id(artifact_id))},
            scope=scope,
            trace=trace,
            effect_kind="durable",
        )
        return MemorySnapshotReceipt.from_mapping(value)

    async def restore(
        self,
        *,
        scope: Scope,
        artifact_id: Id,
        manifest_digest: Digest,
        expected_active_digest: Digest | None,
        trace: Trace,
    ) -> MemoryRestoreReceipt:
        value = await self._client.request(
            "memory.recovery.restore",
            {
                "scope": scope.to_wire(),
                "artifact_id": str(Id(artifact_id)),
                "manifest_digest": str(Digest(manifest_digest)),
                "expected_active_digest": (
                    str(Digest(expected_active_digest))
                    if expected_active_digest is not None
                    else None
                ),
            },
            scope=scope,
            trace=trace,
            effect_kind="durable",
        )
        return MemoryRestoreReceipt.from_mapping(value)


@dataclass(frozen=True, slots=True)
class StartupRecoveryIssue:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise RecoveryError("startup recovery issue is invalid")

    def to_mapping(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_mapping(cls, value: object) -> StartupRecoveryIssue:
        raw = _strict_mapping(value, {"code", "message"}, "startup recovery issue")
        return cls(code=raw["code"], message=raw["message"])


@dataclass(frozen=True, slots=True)
class StartupUnknownEffect:
    effect_id: Id
    module_id: Id
    operation: str
    scope: Scope
    input_digest: Digest
    created_ms: int
    reason: str
    settled_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect_id", Id(self.effect_id))
        object.__setattr__(self, "module_id", Id(self.module_id))
        object.__setattr__(self, "input_digest", Digest(self.input_digest))
        for name in ("created_ms", "settled_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RecoveryError("startup unknown effect timestamp is invalid")
        if not self.operation or not self.reason:
            raise RecoveryError("startup unknown effect is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "effect_id": str(self.effect_id),
            "module_id": str(self.module_id),
            "operation": self.operation,
            "scope": self.scope.to_wire(),
            "input_digest": str(self.input_digest),
            "created_ms": self.created_ms,
            "reason": self.reason,
            "settled_ms": self.settled_ms,
        }

    @classmethod
    def from_mapping(cls, value: object) -> StartupUnknownEffect:
        raw = _strict_mapping(
            value,
            {
                "effect_id",
                "module_id",
                "operation",
                "scope",
                "input_digest",
                "created_ms",
                "reason",
                "settled_ms",
            },
            "startup unknown effect",
        )
        return cls(
            effect_id=Id(raw["effect_id"]),
            module_id=Id(raw["module_id"]),
            operation=raw["operation"],
            scope=Scope.from_wire(raw["scope"]),
            input_digest=Digest(raw["input_digest"]),
            created_ms=raw["created_ms"],
            reason=raw["reason"],
            settled_ms=raw["settled_ms"],
        )


@dataclass(frozen=True, slots=True)
class StartupRecoveryStatus:
    observed_at_ms: int
    mode: str
    memories: tuple[MemoryRecoveryDiagnostics, ...]
    unfinished_migration_versions: tuple[int, ...]
    outbox_cursor: Cursor | None
    unknown_effects: tuple[StartupUnknownEffect, ...]
    issues: tuple[StartupRecoveryIssue, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.observed_at_ms, bool)
            or not isinstance(self.observed_at_ms, int)
            or self.observed_at_ms < 0
            or self.mode not in {"ready", "read_only_recovery"}
            or any(
                isinstance(version, bool) or not isinstance(version, int) or version < 1
                for version in self.unfinished_migration_versions
            )
            or tuple(sorted(set(self.unfinished_migration_versions)))
            != self.unfinished_migration_versions
        ):
            raise RecoveryError("startup recovery status is invalid")
        blocked = bool(self.issues or self.unfinished_migration_versions)
        if (self.mode == "ready") == blocked:
            raise RecoveryError("startup recovery mode does not match its evidence")

    @property
    def requires_operator_decision(self) -> bool:
        return bool(self.unknown_effects)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "observed_at_ms": self.observed_at_ms,
            "mode": self.mode,
            "memories": [memory.to_wire() for memory in self.memories],
            "unfinished_migration_versions": list(self.unfinished_migration_versions),
            "outbox_cursor": (
                self.outbox_cursor.to_wire() if self.outbox_cursor is not None else None
            ),
            "unknown_effects": [effect.to_mapping() for effect in self.unknown_effects],
            "issues": [issue.to_mapping() for issue in self.issues],
        }

    @classmethod
    def from_mapping(cls, value: object) -> StartupRecoveryStatus:
        raw = _strict_mapping(
            value,
            {
                "observed_at_ms",
                "mode",
                "memories",
                "unfinished_migration_versions",
                "outbox_cursor",
                "unknown_effects",
                "issues",
            },
            "startup recovery status",
        )
        for field in (
            "memories",
            "unfinished_migration_versions",
            "unknown_effects",
            "issues",
        ):
            if not isinstance(raw[field], list):
                raise RecoveryError(f"startup recovery {field} must be a list")
        return cls(
            observed_at_ms=raw["observed_at_ms"],
            mode=raw["mode"],
            memories=tuple(
                MemoryRecoveryDiagnostics.from_wire(memory)
                for memory in raw["memories"]
            ),
            unfinished_migration_versions=tuple(raw["unfinished_migration_versions"]),
            outbox_cursor=(
                Cursor.from_wire(raw["outbox_cursor"])
                if raw["outbox_cursor"] is not None
                else None
            ),
            unknown_effects=tuple(
                StartupUnknownEffect.from_mapping(effect)
                for effect in raw["unknown_effects"]
            ),
            issues=tuple(
                StartupRecoveryIssue.from_mapping(issue) for issue in raw["issues"]
            ),
        )


def _strict_mapping(value: object, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RecoveryError(f"{name} fields are not canonical")
    return value


__all__ = [
    "LastKnownGood",
    "QuarantineEntry",
    "RebuiltState",
    "RecoveryBinding",
    "RecoveryError",
    "RecoveryRefusal",
    "RecoveryStore",
    "ReplayRequest",
    "MemoryRecoveryClient",
    "MemoryRestoreReceipt",
    "MemorySnapshotReceipt",
    "StartupRecoveryIssue",
    "StartupRecoveryStatus",
    "StartupUnknownEffect",
    "rebuild_from_journal",
]
