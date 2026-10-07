"""Reviewed operator cutover over Gideon's single writer coordinator."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Mapping, cast

from .client import HypermidClient, HypermidOutcomeUnknown
from .foundation import Cursor, Digest, Id, Scope
from .models import JsonValue
from .writer import (
    CutoverReceipt,
    GideonRestoreReceipt,
    QuiescentJournalBarrier,
    ScopedWriterLease,
    WriterAuthorityStatus,
    WriterCoordinator,
    WriterReconciliation,
    WriterSnapshot,
)

AuthorityAction = Literal["activate_primary", "rollback_to_gideon"]
ReceiptState = Literal["committed", "failed", "outcome_unknown"]
_ACTIONS = frozenset({"activate_primary", "rollback_to_gideon"})


class AuthorityOperationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AuthorityContractError(AuthorityOperationError):
    pass


class AuthorityPlanStale(AuthorityOperationError):
    pass


def _canonical(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise AuthorityContractError("INVALID_PLAN", f"{name} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuthorityContractError("INVALID_PLAN", f"{name} is invalid") from error
    if parsed.tzinfo is None:
        raise AuthorityContractError("INVALID_PLAN", f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _snapshot_wire(snapshot: WriterSnapshot, scope: Scope) -> dict[str, JsonValue]:
    lease = snapshot.lease
    return {
        "scope": scope.to_wire(),
        "mode": snapshot.mode,
        "writer": snapshot.writer,
        "lease_state": snapshot.lease_state,
        "authority_epoch": snapshot.authority_epoch,
        "fence_epoch": lease.fence_epoch if lease is not None else None,
        "cursor": lease.cursor.to_wire() if lease is not None else None,
        "failure": snapshot.failure,
    }


@dataclass(frozen=True, slots=True)
class AuthorityStatus:
    scope: Scope
    mode: str
    writer: str
    lease_state: str
    owns_writes: bool
    authority_epoch: int
    fence_epoch: int | None
    cursor: Cursor | None
    failure: str | None
    authority_digest: str

    @classmethod
    def from_snapshot(cls, snapshot: WriterSnapshot, scope: Scope) -> AuthorityStatus:
        wire = _snapshot_wire(snapshot, scope)
        return cls(
            scope=scope,
            mode=snapshot.mode,
            writer=snapshot.writer,
            lease_state=snapshot.lease_state,
            owns_writes=snapshot.owns_writes,
            authority_epoch=snapshot.authority_epoch,
            fence_epoch=cast(int | None, wire["fence_epoch"]),
            cursor=snapshot.lease.cursor if snapshot.lease is not None else None,
            failure=snapshot.failure,
            authority_digest=_digest(wire),
        )

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "scope": self.scope.to_wire(),
            "mode": self.mode,
            "writer": self.writer,
            "lease_state": self.lease_state,
            "owns_writes": self.owns_writes,
            "authority_epoch": self.authority_epoch,
            "fence_epoch": self.fence_epoch,
            "cursor": self.cursor.to_wire() if self.cursor is not None else None,
            "failure": self.failure,
            "authority_digest": self.authority_digest,
        }


@dataclass(frozen=True, slots=True)
class AuthorityPlan:
    plan_id: Id
    action: AuthorityAction
    scope: Scope
    created_at: datetime
    expires_at: datetime
    authority_digest: str
    blockers: tuple[str, ...]
    steps: tuple[str, ...]
    plan_digest: str

    def _unsigned_wire(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "plan_id": str(self.plan_id),
            "action": self.action,
            "scope": self.scope.to_wire(),
            "created_at": _timestamp(self.created_at),
            "expires_at": _timestamp(self.expires_at),
            "authority_digest": self.authority_digest,
            "blockers": list(self.blockers),
            "steps": list(self.steps),
        }

    def to_wire(self) -> dict[str, JsonValue]:
        return self._unsigned_wire() | {"plan_digest": self.plan_digest}

    @classmethod
    def create(
        cls,
        *,
        action: AuthorityAction,
        scope: Scope,
        status: AuthorityStatus,
        blockers: tuple[str, ...],
        now: datetime,
        ttl: timedelta,
    ) -> AuthorityPlan:
        steps = (
            (
                "flush_journals",
                "quiesce_background",
                "validate_source_digests",
                "acquire_writer_lease",
                "install_primary_engine",
            )
            if action == "activate_primary"
            else ("uninstall_primary_engine", "release_writer_lease", "resume_gideon")
        )
        provisional = cls(
            plan_id=Id(f"authority:{secrets.token_hex(16)}"),
            action=action,
            scope=scope,
            created_at=now,
            expires_at=now + ttl,
            authority_digest=status.authority_digest,
            blockers=blockers,
            steps=steps,
            plan_digest="",
        )
        return replace(provisional, plan_digest=_digest(provisional._unsigned_wire()))

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> AuthorityPlan:
        if not isinstance(value, Mapping):
            raise AuthorityContractError(
                "INVALID_PLAN", "authority plan must be an object"
            )
        expected = {
            "schema_version",
            "plan_id",
            "action",
            "scope",
            "created_at",
            "expires_at",
            "authority_digest",
            "blockers",
            "steps",
            "plan_digest",
        }
        if set(value) != expected or value.get("schema_version") != 1:
            raise AuthorityContractError(
                "INVALID_PLAN", "authority plan fields do not match schema version 1"
            )
        action = value.get("action")
        if action not in _ACTIONS:
            raise AuthorityContractError("INVALID_PLAN", "authority action is invalid")
        try:
            scope = Scope.from_wire(value.get("scope"))
            plan_id = Id(value.get("plan_id"))
        except (TypeError, ValueError) as error:
            raise AuthorityContractError(
                "INVALID_PLAN", "authority plan identity is invalid"
            ) from error
        if scope != expected_scope:
            raise AuthorityContractError(
                "SCOPE_MISMATCH",
                "authority plan scope does not match the active runtime",
            )
        blockers = value.get("blockers")
        steps = value.get("steps")
        if not isinstance(blockers, list) or not all(
            isinstance(item, str) and item for item in blockers
        ):
            raise AuthorityContractError(
                "INVALID_PLAN", "authority plan blockers are invalid"
            )
        if (
            not isinstance(steps, list)
            or not steps
            or not all(isinstance(item, str) and item for item in steps)
        ):
            raise AuthorityContractError(
                "INVALID_PLAN", "authority plan steps are invalid"
            )
        authority_digest = value.get("authority_digest")
        plan_digest = value.get("plan_digest")
        if not all(_is_digest(item) for item in (authority_digest, plan_digest)):
            raise AuthorityContractError(
                "INVALID_PLAN", "authority plan digests are invalid"
            )
        plan = cls(
            plan_id=plan_id,
            action=action,
            scope=scope,
            created_at=_parse_timestamp(value.get("created_at"), "created_at"),
            expires_at=_parse_timestamp(value.get("expires_at"), "expires_at"),
            authority_digest=cast(str, authority_digest),
            blockers=tuple(blockers),
            steps=tuple(steps),
            plan_digest=cast(str, plan_digest),
        )
        if _digest(plan._unsigned_wire()) != plan.plan_digest:
            raise AuthorityContractError(
                "PLAN_TAMPERED", "authority plan digest does not match its contents"
            )
        return plan


@dataclass(frozen=True, slots=True)
class AuthorityReceipt:
    operation_id: Id
    action: AuthorityAction
    scope: Scope
    plan_digest: str
    state: ReceiptState
    status: AuthorityStatus
    finished_at: datetime
    error_code: str | None = None
    error_message: str | None = None

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "operation_id": str(self.operation_id),
            "action": self.action,
            "scope": self.scope.to_wire(),
            "plan_digest": self.plan_digest,
            "state": self.state,
            "status": self.status.to_wire(),
            "finished_at": _timestamp(self.finished_at),
            "error": (
                {"code": self.error_code, "message": self.error_message}
                if self.error_code
                else None
            ),
        }


class DaemonWriterLeaseAuthority:
    """Use the daemon's durable scoped authority without caching lease state."""

    def __init__(self, client: HypermidClient) -> None:
        self.client = client

    async def acquire(
        self, *, scope: Scope, request_id: Id, minimum_fence_epoch: int
    ) -> ScopedWriterLease:
        if scope != self.client.scope:
            raise AuthorityContractError(
                "SCOPE_MISMATCH",
                "writer acquire scope does not match the authenticated client",
            )
        value = await self.client.request(
            "writer.acquire",
            {"request_id": str(request_id), "minimum_fence_epoch": minimum_fence_epoch},
            effect_kind="durable",
        )
        return self._lease(value)

    async def cutover(
        self,
        *,
        scope: Scope,
        request_id: Id,
        lease: ScopedWriterLease,
        barrier: QuiescentJournalBarrier,
    ) -> CutoverReceipt:
        self._require_scope(scope, lease)
        value = await self.client.request(
            "writer.cutover",
            {
                "request_id": str(request_id),
                "lease": cast(dict[str, JsonValue], lease.to_wire()),
                "barrier": cast(dict[str, JsonValue], barrier.to_wire()),
            },
            effect_kind="durable",
        )
        return self._cutover(value)

    async def restore(
        self,
        *,
        scope: Scope,
        request_id: Id,
        lease: ScopedWriterLease,
        barrier: QuiescentJournalBarrier,
    ) -> GideonRestoreReceipt:
        self._require_scope(scope, lease)
        value = await self.client.request(
            "writer.restore",
            {
                "request_id": str(request_id),
                "lease": cast(dict[str, JsonValue], lease.to_wire()),
                "barrier": cast(dict[str, JsonValue], barrier.to_wire()),
            },
            effect_kind="durable",
        )
        return self._restore(value)

    async def reconcile(self, request_id: Id) -> WriterReconciliation | None:
        value = await self.client.request(
            "writer.reconcile", {"request_id": str(request_id)}
        )
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != {"operation", "result"}:
            raise AuthorityContractError(
                "INVALID_RECONCILIATION", "daemon writer reconciliation is invalid"
            )
        operation = value.get("operation")
        result = value.get("result")
        if operation == "acquire":
            return self._lease(result)
        if operation == "cutover":
            return self._cutover(result)
        if operation in {"release", "restore"}:
            return self._restore(result)
        raise AuthorityContractError(
            "INVALID_RECONCILIATION",
            "daemon writer reconciliation operation is invalid",
        )

    async def status(self, *, scope: Scope) -> WriterAuthorityStatus:
        if scope != self.client.scope:
            raise AuthorityContractError(
                "SCOPE_MISMATCH",
                "writer status scope does not match the authenticated client",
            )
        value = await self.client.request("writer.status", {})
        if not isinstance(value, Mapping):
            raise AuthorityContractError(
                "INVALID_STATUS", "daemon writer status must be an object"
            )
        required = {"scope", "authority", "authority_epoch", "cursor"}
        if not required.issubset(value) or not set(value).issubset(
            required | {"active_lease", "journal_digest"}
        ):
            raise AuthorityContractError(
                "INVALID_STATUS", "daemon writer status fields are invalid"
            )
        try:
            returned_scope = Scope.from_wire(value.get("scope"))
        except (TypeError, ValueError) as error:
            raise AuthorityContractError(
                "INVALID_STATUS", "daemon writer status scope is invalid"
            ) from error
        if returned_scope != self.client.scope:
            raise AuthorityContractError(
                "SCOPE_MISMATCH", "daemon returned foreign writer status"
            )
        authority = value.get("authority")
        authority_epoch = value.get("authority_epoch")
        if authority not in {"gideon", "hypermid_pending", "hypermid"}:
            raise AuthorityContractError(
                "INVALID_STATUS", "daemon writer authority is invalid"
            )
        if (
            isinstance(authority_epoch, bool)
            or not isinstance(authority_epoch, int)
            or authority_epoch < 1
        ):
            raise AuthorityContractError(
                "INVALID_STATUS", "daemon writer authority epoch is invalid"
            )
        try:
            cursor = Cursor.from_wire(value.get("cursor"))
            active = (
                self._lease(value["active_lease"]) if "active_lease" in value else None
            )
            journal = (
                Digest(value["journal_digest"]) if "journal_digest" in value else None
            )
        except (TypeError, ValueError) as error:
            raise AuthorityContractError(
                "INVALID_STATUS", "daemon writer status evidence is invalid"
            ) from error
        if authority != "gideon" and (
            active is None or active.fence_epoch != authority_epoch
        ):
            raise AuthorityContractError(
                "INVALID_STATUS",
                "daemon active writer status is missing its fenced lease",
            )
        return WriterAuthorityStatus(
            returned_scope,
            cast(Literal["gideon", "hypermid_pending", "hypermid"], authority),
            authority_epoch,
            cursor,
            active,
            journal,
        )

    def _require_scope(self, scope: Scope, lease: ScopedWriterLease) -> None:
        if scope != self.client.scope or lease.scope != self.client.scope:
            raise AuthorityContractError(
                "SCOPE_MISMATCH",
                "writer operation scope does not match the authenticated client",
            )

    def _lease(self, value: object) -> ScopedWriterLease:
        if not isinstance(value, Mapping) or set(value) != {
            "lease_id",
            "scope",
            "fence_epoch",
            "fence_token",
            "cursor",
        }:
            raise AuthorityContractError(
                "INVALID_LEASE", "daemon writer lease must be an object"
            )
        try:
            scope = Scope.from_wire(value.get("scope"))
            lease = ScopedWriterLease(
                lease_id=Id(value.get("lease_id")),
                scope=scope,
                fence_epoch=cast(int, value.get("fence_epoch")),
                fence_token=Id(value.get("fence_token")),
                cursor=Cursor.from_wire(value.get("cursor")),
            )
        except (TypeError, ValueError) as error:
            raise AuthorityContractError(
                "INVALID_LEASE", "daemon returned an invalid writer lease"
            ) from error
        if lease.scope != self.client.scope:
            raise AuthorityContractError(
                "SCOPE_MISMATCH", "daemon returned a foreign writer lease"
            )
        return lease

    def _cutover(self, value: object) -> CutoverReceipt:
        if not isinstance(value, Mapping) or set(value) != {
            "request_id",
            "lease",
            "journal_digest",
            "cursor",
        }:
            raise AuthorityContractError(
                "INVALID_CUTOVER", "daemon cutover receipt must be an object"
            )
        try:
            receipt = CutoverReceipt(
                request_id=Id(value.get("request_id")),
                lease=self._lease(value.get("lease")),
                journal_digest=Digest(value.get("journal_digest")),
                cursor=Cursor.from_wire(value.get("cursor")),
            )
        except (TypeError, ValueError) as error:
            raise AuthorityContractError(
                "INVALID_CUTOVER", "daemon returned an invalid cutover receipt"
            ) from error
        return receipt

    def _restore(self, value: object) -> GideonRestoreReceipt:
        expected = {
            "request_id",
            "scope",
            "prior_lease_id",
            "prior_fence_epoch",
            "prior_fence_token",
            "gideon_epoch",
            "cursor",
            "journal_digest",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise AuthorityContractError(
                "INVALID_RESTORE", "daemon restore receipt must be an object"
            )
        prior_epoch = value.get("prior_fence_epoch")
        gideon_epoch = value.get("gideon_epoch")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1
            for item in (prior_epoch, gideon_epoch)
        ):
            raise AuthorityContractError(
                "INVALID_RESTORE", "daemon restore epochs are invalid"
            )
        try:
            receipt = GideonRestoreReceipt(
                request_id=Id(value.get("request_id")),
                scope=Scope.from_wire(value.get("scope")),
                prior_lease_id=Id(value.get("prior_lease_id")),
                prior_fence_epoch=cast(int, prior_epoch),
                prior_fence_token=Id(value.get("prior_fence_token")),
                gideon_epoch=cast(int, gideon_epoch),
                cursor=Cursor.from_wire(value.get("cursor")),
                journal_digest=Digest(value.get("journal_digest")),
            )
        except (TypeError, ValueError) as error:
            raise AuthorityContractError(
                "INVALID_RESTORE", "daemon returned an invalid restore receipt"
            ) from error
        if (
            receipt.scope != self.client.scope
            or receipt.gideon_epoch <= receipt.prior_fence_epoch
        ):
            raise AuthorityContractError(
                "INVALID_RESTORE",
                "daemon restore receipt does not prove scoped higher-epoch handback",
            )
        return receipt


class AuthorityOperations:
    def __init__(self, coordinator: WriterCoordinator) -> None:
        self.coordinator = coordinator

    async def status(self, *, reconcile_unknown: bool = False) -> AuthorityStatus:
        snapshot = self.coordinator.snapshot()
        if reconcile_unknown and snapshot.lease_state == "unknown":
            snapshot = await self.coordinator.reconcile_unknown()
        try:
            await self.coordinator.verify_durable_status()
        except RuntimeError as error:
            raise AuthorityOperationError("AUTHORITY_DIVERGED", str(error)) from error
        snapshot = self.coordinator.snapshot()
        return AuthorityStatus.from_snapshot(snapshot, self.coordinator.scope)

    async def plan(
        self, action: AuthorityAction, *, ttl_seconds: int = 300
    ) -> AuthorityPlan:
        if action not in _ACTIONS:
            raise AuthorityContractError(
                "INVALID_ACTION", "authority action is invalid"
            )
        if isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 900:
            raise AuthorityContractError(
                "INVALID_TTL", "authority plan ttl must be between 30 and 900 seconds"
            )
        status = await self.status()
        blockers: list[str] = []
        if status.lease_state == "unknown":
            blockers.append(
                "The previous writer mutation has an unknown outcome and must be reconciled."
            )
        if action == "activate_primary" and status.mode != "primary":
            blockers.append(
                "Runtime mode must be primary before writer authority can be transferred."
            )
        now = datetime.now(UTC)
        return AuthorityPlan.create(
            action=action,
            scope=self.coordinator.scope,
            status=status,
            blockers=tuple(blockers),
            now=now,
            ttl=timedelta(seconds=ttl_seconds),
        )

    async def apply(self, value: object, *, reviewed_digest: str) -> AuthorityReceipt:
        plan = AuthorityPlan.from_wire(value, self.coordinator.scope)
        if reviewed_digest != plan.plan_digest:
            raise AuthorityContractError(
                "REVIEW_MISMATCH", "reviewed digest does not match the authority plan"
            )
        if datetime.now(UTC) >= plan.expires_at:
            raise AuthorityPlanStale(
                "PLAN_EXPIRED", "authority plan expired before apply"
            )
        if plan.blockers:
            raise AuthorityPlanStale(
                "PLAN_BLOCKED", "a blocked authority plan cannot be applied"
            )
        before = await self.status()
        if before.authority_digest != plan.authority_digest:
            raise AuthorityPlanStale(
                "PLAN_STALE", "writer authority changed after plan review"
            )
        operation_id = Id(f"authority-op:{secrets.token_hex(16)}")
        try:
            snapshot = (
                await self.coordinator.activate_primary()
                if plan.action == "activate_primary"
                else await self.coordinator.deactivate()
            )
        except HypermidOutcomeUnknown as error:
            status = await self.status()
            return AuthorityReceipt(
                operation_id,
                plan.action,
                plan.scope,
                plan.plan_digest,
                "outcome_unknown",
                status,
                datetime.now(UTC),
                "OUTCOME_UNKNOWN",
                str(error),
            )
        except Exception as error:
            status = await self.status()
            return AuthorityReceipt(
                operation_id,
                plan.action,
                plan.scope,
                plan.plan_digest,
                "failed",
                status,
                datetime.now(UTC),
                type(error).__name__.upper()[:64],
                str(error)[:240],
            )
        status = AuthorityStatus.from_snapshot(snapshot, self.coordinator.scope)
        if plan.action == "activate_primary" and not status.owns_writes:
            raise AuthorityOperationError(
                "AUTHORITY_NOT_TRANSFERRED",
                "primary activation returned without writer ownership",
            )
        if plan.action == "rollback_to_gideon" and status.writer != "gideon":
            raise AuthorityOperationError(
                "AUTHORITY_NOT_RELEASED",
                "rollback returned without Gideon writer ownership",
            )
        return AuthorityReceipt(
            operation_id,
            plan.action,
            plan.scope,
            plan.plan_digest,
            "committed",
            status,
            datetime.now(UTC),
        )


def authority_service_for(lifecycle: object) -> AuthorityOperations:
    coordinator = getattr(lifecycle, "writer", None)
    if not isinstance(coordinator, WriterCoordinator):
        raise AuthorityOperationError(
            "AUTHORITY_UNAVAILABLE", "the runtime has no bound writer coordinator"
        )
    return AuthorityOperations(coordinator)


service_for = authority_service_for


__all__ = [
    "AuthorityAction",
    "AuthorityContractError",
    "AuthorityOperationError",
    "AuthorityOperations",
    "AuthorityPlan",
    "AuthorityPlanStale",
    "AuthorityReceipt",
    "AuthorityStatus",
    "DaemonWriterLeaseAuthority",
    "authority_service_for",
    "service_for",
]
