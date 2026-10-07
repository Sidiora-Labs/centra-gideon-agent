from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .protocol import Cursor, Scope, WriterLease, _require_id


class OwnershipError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    operation: str
    cursor: Cursor
    result: Any
    replayed: bool = False


@dataclass(slots=True)
class SessionState:
    session_id: str
    scope: Scope
    cursor: Cursor = field(default_factory=lambda: Cursor(epoch=1, sequence=0))
    fence_epoch: int = 0
    active_lease: WriterLease | None = None
    lease_expires_at_ms: int = 0
    mutations: dict[str, MutationOutcome] = field(default_factory=dict)


def _timestamp(epoch_ms: int) -> str:
    return (
        datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class OwnershipFence:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def bind(self, session_id: str, scope: Scope) -> SessionState:
        _require_id(session_id, "session_id")
        state = self._sessions.get(session_id)
        if state is None:
            state = SessionState(session_id=session_id, scope=scope)
            self._sessions[session_id] = state
            return copy.deepcopy(state)
        self._require_scope(state, scope)
        return copy.deepcopy(state)

    def state(self, session_id: str, scope: Scope) -> SessionState:
        state = self._require_session(session_id)
        self._require_scope(state, scope)
        return copy.deepcopy(state)

    def acquire_writer(
        self,
        session_id: str,
        scope: Scope,
        lease_id: str,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> WriterLease:
        _require_id(lease_id, "lease_id")
        if ttl_ms <= 0:
            raise OwnershipError("INVALID_LEASE", "lease ttl must be positive")
        state = self._require_session(session_id)
        self._require_scope(state, scope)
        state.fence_epoch += 1
        state.lease_expires_at_ms = now_ms + ttl_ms
        state.active_lease = WriterLease(
            lease_id=lease_id,
            session_id=session_id,
            scope=scope,
            fence_token=f"fence:{state.fence_epoch}",
            acquired_at=_timestamp(now_ms),
            expires_at=_timestamp(state.lease_expires_at_ms),
            cursor=state.cursor,
        )
        return state.active_lease

    def rebind_workspace(
        self,
        session_id: str,
        previous_scope: Scope,
        next_scope: Scope,
        expected_cursor: Cursor,
        lease: WriterLease,
        idempotency_key: str,
        *,
        now_ms: int,
    ) -> MutationOutcome:
        if (
            previous_scope.owner_id != next_scope.owner_id
            or previous_scope.project_id != next_scope.project_id
        ):
            raise OwnershipError(
                "IDENTITY_CHANGE_REQUIRES_NEW_SESSION",
                "owner and project changes require a distinct context identity",
            )
        state = self._require_session(session_id)
        previous = state.mutations.get(idempotency_key)
        if previous is not None:
            if previous.operation != "rebind" or state.scope != next_scope:
                raise OwnershipError(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key was used for another mutation",
                )
            return MutationOutcome(
                operation=previous.operation,
                cursor=previous.cursor,
                result=copy.deepcopy(previous.result),
                replayed=True,
            )
        outcome = self.commit(
            session_id,
            previous_scope,
            expected_cursor,
            lease,
            idempotency_key,
            "rebind",
            {
                "previous_scope": previous_scope.to_wire(),
                "next_scope": next_scope.to_wire(),
            },
            now_ms=now_ms,
        )
        state = self._sessions[session_id]
        state.scope = next_scope
        if state.active_lease is not None:
            state.active_lease = WriterLease(
                lease_id=state.active_lease.lease_id,
                session_id=session_id,
                scope=next_scope,
                fence_token=state.active_lease.fence_token,
                acquired_at=state.active_lease.acquired_at,
                expires_at=state.active_lease.expires_at,
                cursor=state.cursor,
            )
        return outcome

    def commit(
        self,
        session_id: str,
        scope: Scope,
        expected_cursor: Cursor,
        lease: WriterLease,
        idempotency_key: str,
        operation: str,
        result: Any,
        *,
        now_ms: int,
    ) -> MutationOutcome:
        _require_id(idempotency_key, "idempotency_key")
        state = self._require_session(session_id)
        self._require_scope(state, scope)
        previous = state.mutations.get(idempotency_key)
        if previous is not None:
            if previous.operation != operation:
                raise OwnershipError(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key was used for another operation",
                )
            return MutationOutcome(
                operation=previous.operation,
                cursor=previous.cursor,
                result=copy.deepcopy(previous.result),
                replayed=True,
            )
        self._require_lease(state, lease, now_ms)
        if expected_cursor != state.cursor:
            raise OwnershipError("STALE_CURSOR", "expected cursor is not current")
        next_cursor = Cursor(
            epoch=state.cursor.epoch, sequence=state.cursor.sequence + 1
        )
        outcome = MutationOutcome(
            operation=operation,
            cursor=next_cursor,
            result=copy.deepcopy(result),
        )
        state.cursor = next_cursor
        state.mutations[idempotency_key] = outcome
        if state.active_lease is not None:
            state.active_lease = WriterLease(
                lease_id=state.active_lease.lease_id,
                session_id=state.active_lease.session_id,
                scope=state.active_lease.scope,
                fence_token=state.active_lease.fence_token,
                acquired_at=state.active_lease.acquired_at,
                expires_at=state.active_lease.expires_at,
                cursor=next_cursor,
            )
        return copy.deepcopy(outcome)

    def _require_session(self, session_id: str) -> SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            raise OwnershipError("SESSION_NOT_BOUND", "session is not bound")
        return state

    @staticmethod
    def _require_scope(state: SessionState, scope: Scope) -> None:
        if state.scope != scope:
            raise OwnershipError("SCOPE_MISMATCH", "scope does not own this session")

    @staticmethod
    def _require_lease(state: SessionState, lease: WriterLease, now_ms: int) -> None:
        active = state.active_lease
        if active is None:
            raise OwnershipError("WRITER_LEASE_REQUIRED", "no writer lease is active")
        if (
            lease.lease_id != active.lease_id
            or lease.fence_token != active.fence_token
            or lease.session_id != state.session_id
            or lease.scope != state.scope
        ):
            raise OwnershipError("STALE_FENCE", "writer lease was superseded")
        if now_ms >= state.lease_expires_at_ms:
            raise OwnershipError("LEASE_EXPIRED", "writer lease has expired")
