from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, TypeAlias, cast

from .bus import BusMessage
from .compaction import CompactionProvider
from .foundation import Cursor, Digest, EffectState, Error, Scope, Trace
from .observability import (
    DailySegmentWriter,
    LogLevel,
    LogRecord,
    RedactionPolicy,
    format_line,
    redact_record,
)
from .storage import Fence, LeaseKey, Migration, SQLiteStore


class RoleProvider(Protocol):
    def handle(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]: ...


class BusPublisher(Protocol):
    def publish(self, message: BusMessage, payload: bytes) -> Cursor: ...


@dataclass(frozen=True, slots=True)
class RoleDiscovery:
    role: str
    version: str
    operations: tuple[str, ...]
    stability: str


@dataclass(frozen=True, slots=True)
class RoleOutcome:
    cursor: Cursor
    value: Mapping[str, Any]


FoundationResult: TypeAlias = Cursor | RoleOutcome | Error


_MIGRATIONS = (
    Migration(
        1,
        "foundation_service_events",
        """
        CREATE TABLE hypermid_foundation_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('intent', 'committed', 'unknown')),
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            value_json TEXT,
            external_cursor_epoch INTEGER,
            external_cursor_sequence INTEGER,
            UNIQUE(kind, request_id)
        );
        """,
    ),
)

_BUS_MIGRATIONS = (
    Migration(
        1,
        "foundation_local_bus",
        """
        CREATE TABLE hypermid_foundation_bus (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL,
            digest TEXT NOT NULL,
            headers_json TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            payload BLOB NOT NULL
        );
        """,
    ),
)


class FoundationLocalBus:
    def __init__(self, scope: Scope, store: SQLiteStore) -> None:
        self._scope = scope
        self._store = store

    @classmethod
    def open(cls, state_root: str | Path, scope: Scope) -> FoundationLocalBus:
        root = Path(state_root)
        if not root.is_absolute():
            raise ValueError("local bus state root must be absolute")
        store = SQLiteStore.open(
            root / "foundation-bus.sqlite3",
            LeaseKey("hypermid-foundation-bus", "sqlite", _scope_key(scope)),
            len(_BUS_MIGRATIONS),
            _BUS_MIGRATIONS,
        )
        return cls(scope, store)

    def publish(self, message: BusMessage, payload: bytes) -> Cursor:
        if message.scope != self._scope:
            raise PermissionError("message scope does not own this local bus")
        if not isinstance(payload, bytes) or Digest.sha256(payload) != message.digest:
            raise ValueError("message digest does not match its stored payload")

        def write(connection: Any) -> int:
            existing = connection.execute(
                "SELECT sequence, digest, payload FROM hypermid_foundation_bus WHERE message_id=?",
                (str(message.id),),
            ).fetchone()
            if existing is not None:
                if existing[1] != str(message.digest) or bytes(existing[2]) != payload:
                    raise ValueError("message Id was reused with different content")
                return int(existing[0])
            return int(
                connection.execute(
                    """
                    INSERT INTO hypermid_foundation_bus(
                        message_id, subject, digest, headers_json,
                        trace_id, request_id, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(message.id),
                        message.subject,
                        str(message.digest),
                        json.dumps(
                            dict(message.headers), separators=(",", ":"), sort_keys=True
                        ),
                        str(message.trace.trace_id),
                        str(message.trace.request_id),
                        payload,
                    ),
                ).lastrowid
            )

        sequence = self._store.fenced_transaction(self._store.fence, write)
        return Cursor(self._store.fence.epoch, sequence)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> FoundationLocalBus:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class FoundationService:
    def __init__(
        self,
        *,
        scope: Scope,
        store: SQLiteStore,
        bus: BusPublisher,
        compaction: CompactionProvider,
        transform: RoleProvider,
        log_writer: DailySegmentWriter,
        redaction: RedactionPolicy,
    ) -> None:
        self._scope = scope
        self._store = store
        self._bus = bus
        self._compaction = compaction
        self._transform = transform
        self._log_writer = log_writer
        self._redaction = redaction

    @classmethod
    def open(
        cls,
        state_root: str | Path,
        scope: Scope,
        *,
        bus: BusPublisher,
        compaction: CompactionProvider,
        transform: RoleProvider,
        log_writer: DailySegmentWriter,
        redaction: RedactionPolicy | None = None,
    ) -> FoundationService:
        root = Path(state_root)
        if not root.is_absolute():
            raise ValueError("foundation state root must be absolute")
        scope_key = _scope_key(scope)
        store = SQLiteStore.open(
            root / "foundation.sqlite3",
            LeaseKey("hypermid-foundation-service", "sqlite", scope_key),
            len(_MIGRATIONS),
            _MIGRATIONS,
        )
        return cls(
            scope=scope,
            store=store,
            bus=bus,
            compaction=compaction,
            transform=transform,
            log_writer=log_writer,
            redaction=redaction or RedactionPolicy(),
        )

    @property
    def fence(self) -> Fence:
        return self._store.fence

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> FoundationService:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def discover_roles(self, scope: Scope) -> tuple[RoleDiscovery, ...] | Error:
        denied = self._scope_error(scope)
        if denied is not None:
            return denied
        discovered: list[RoleDiscovery] = []
        for role, provider in (
            ("compaction", self._compaction),
            ("transform", self._transform),
        ):
            try:
                description = provider.handle("describe", {})
                majors = description.get("majors")
                if not isinstance(majors, list) or not majors:
                    raise ValueError("role description is incomplete")
                for major in majors:
                    if not isinstance(major, Mapping):
                        raise ValueError("role major is malformed")
                    version = major.get("version")
                    operations = major.get("ops")
                    stability = major.get("stability")
                    if (
                        not isinstance(version, str)
                        or not isinstance(operations, list)
                        or not all(isinstance(item, str) for item in operations)
                        or stability not in {"alpha", "beta", "stable"}
                    ):
                        raise ValueError("role major is malformed")
                    discovered.append(
                        RoleDiscovery(
                            role=role,
                            version=version,
                            operations=tuple(sorted(set(operations))),
                            stability=stability,
                        )
                    )
            except Exception:
                return _error(
                    "ROLE_DISCOVERY_FAILED",
                    "a configured role did not provide a valid descriptor",
                    retryable=False,
                    effect_state=EffectState.NOT_STARTED,
                )
        return tuple(discovered)

    def exchange(
        self,
        scope: Scope,
        message: BusMessage,
        payload: bytes,
    ) -> Cursor | Error:
        denied = self._scope_error(scope)
        if denied is not None:
            return denied
        if message.scope != self._scope:
            return self._denied()
        trace = message.trace
        intent = self._record_intent("bus.exchange", trace)
        if isinstance(intent, Error):
            return intent
        try:
            external = self._bus.publish(message, payload)
        except Exception:
            self._settle(intent, "unknown", None, None)
            return _error(
                "BUS_OUTCOME_UNKNOWN",
                "the bus exchange outcome requires reconciliation",
                retryable=False,
                effect_state=EffectState.UNKNOWN,
            )
        return self._settle(intent, "committed", None, external)

    def invoke_compaction(
        self,
        scope: Scope,
        method: str,
        params: Mapping[str, Any],
        trace: Trace,
    ) -> RoleOutcome | Error:
        return self._invoke_role(
            scope, "compaction", self._compaction, method, params, trace
        )

    def invoke_transform(
        self,
        scope: Scope,
        method: str,
        params: Mapping[str, Any],
        trace: Trace,
    ) -> RoleOutcome | Error:
        return self._invoke_role(
            scope, "transform", self._transform, method, params, trace
        )

    def emit_trace(
        self,
        scope: Scope,
        trace: Trace,
        fields: Mapping[str, Any],
        *,
        timestamp: str | None = None,
        utc_date: str | None = None,
        level: LogLevel = LogLevel.INFO,
        message: str = "foundation operation",
    ) -> Cursor | Error:
        denied = self._scope_error(scope)
        if denied is not None:
            return denied
        intent = self._record_intent("trace.emit", trace)
        if isinstance(intent, Error):
            return intent
        now = datetime.now(UTC)
        record = redact_record(
            LogRecord(
                timestamp=timestamp
                or now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                level=level,
                logger="hypermid.foundation",
                message=message,
                bound=(("trace_id", str(trace.trace_id)),),
                fields=fields,
            ),
            self._redaction,
        )
        try:
            self._log_writer.append(
                utc_date or now.date().isoformat(), format_line(record)
            )
        except Exception:
            self._settle(intent, "unknown", None, None)
            return _error(
                "TRACE_OUTCOME_UNKNOWN",
                "the trace append outcome requires reconciliation",
                retryable=False,
                effect_state=EffectState.UNKNOWN,
            )
        return self._settle(intent, "committed", None, None)

    def _invoke_role(
        self,
        scope: Scope,
        role: str,
        provider: RoleProvider,
        method: str,
        params: Mapping[str, Any],
        trace: Trace,
    ) -> RoleOutcome | Error:
        denied = self._scope_error(scope)
        if denied is not None:
            return denied
        discovery = self.discover_roles(scope)
        if isinstance(discovery, Error):
            return discovery
        role_description = next(
            (
                item
                for item in discovery
                if item.role == role and method in item.operations
            ),
            None,
        )
        if role_description is None:
            return _error(
                "ROLE_OPERATION_UNSUPPORTED",
                "the requested role operation is not declared",
                retryable=False,
                effect_state=EffectState.NOT_STARTED,
            )
        intent = self._record_intent(f"role.{role}.{method}", trace)
        if isinstance(intent, Error):
            return intent
        try:
            value = provider.handle(method, params)
            if not isinstance(value, Mapping):
                raise ValueError("role result is not an object")
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except Exception:
            cursor = self._settle(intent, "committed", None, None)
            if isinstance(cursor, Error):
                return cursor
            return _error(
                "ROLE_REFUSED",
                "the role refused the request",
                retryable=False,
                effect_state=EffectState.NOT_STARTED,
            )
        cursor = self._settle(intent, "committed", canonical, None)
        if isinstance(cursor, Error):
            return cursor
        return RoleOutcome(cursor, dict(value))

    def _record_intent(self, kind: str, trace: Trace) -> int | Error:
        try:
            return cast(
                int,
                self._store.fenced_transaction(
                    self._store.fence,
                    lambda connection: connection.execute(
                        """
                    INSERT INTO hypermid_foundation_events(
                        kind, state, trace_id, request_id
                    ) VALUES (?, 'intent', ?, ?)
                    """,
                        (kind, str(trace.trace_id), str(trace.request_id)),
                    ).lastrowid,
                ),
            )
        except Exception:
            return _error(
                "FOUNDATION_INTENT_FAILED",
                "the operation could not be recorded before dispatch",
                retryable=True,
                effect_state=EffectState.NOT_STARTED,
            )

    def _settle(
        self,
        sequence: int,
        state: str,
        value_json: str | None,
        external_cursor: Cursor | None,
    ) -> Cursor | Error:
        try:
            self._store.fenced_transaction(
                self._store.fence,
                lambda connection: connection.execute(
                    """
                    UPDATE hypermid_foundation_events
                    SET state=?, value_json=?, external_cursor_epoch=?,
                        external_cursor_sequence=?
                    WHERE sequence=? AND state='intent'
                    """,
                    (
                        state,
                        value_json,
                        external_cursor.epoch if external_cursor else None,
                        external_cursor.sequence if external_cursor else None,
                        sequence,
                    ),
                ),
            )
            return Cursor(self._store.fence.epoch, sequence)
        except Exception:
            return _error(
                "FOUNDATION_SETTLEMENT_FAILED",
                "the operation outcome requires reconciliation",
                retryable=False,
                effect_state=EffectState.UNKNOWN,
            )

    def _scope_error(self, scope: Scope) -> Error | None:
        return None if scope == self._scope else self._denied()

    @staticmethod
    def _denied() -> Error:
        return _error(
            "AUTHORIZATION_DENIED",
            "the authenticated scope does not own this foundation service",
            retryable=False,
            effect_state=EffectState.NOT_STARTED,
        )


def _scope_key(scope: Scope) -> str:
    parts = [str(scope.owner_id), str(scope.project_id)]
    if scope.workspace_id is not None:
        parts.append(str(scope.workspace_id))
    return ":".join(parts)


def _error(
    code: str,
    message: str,
    *,
    retryable: bool,
    effect_state: EffectState,
) -> Error:
    return Error(code, message, retryable, effect_state=effect_state)


__all__ = [
    "BusPublisher",
    "FoundationResult",
    "FoundationLocalBus",
    "FoundationService",
    "RoleDiscovery",
    "RoleOutcome",
    "RoleProvider",
]
