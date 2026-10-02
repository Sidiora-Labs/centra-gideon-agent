"""Single-writer cutover barrier for opt-in Hypermid primary mode."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, Sequence

from gideon.cognition.bg_compress import (
    quiesce_background_compression,
    resume_background_compression,
)
from gideon.cognition.history import ConversationCheckpoint, ConversationLog

from .client import HypermidOutcomeUnknown
from .context import ConversationContextBridge
from .foundation import Cursor, Digest, Id, Scope


@dataclass(frozen=True, slots=True)
class ScopedWriterLease:
    lease_id: Id
    scope: Scope
    fence_epoch: int
    fence_token: Id
    cursor: Cursor

    def __post_init__(self) -> None:
        object.__setattr__(self, "lease_id", Id(self.lease_id))
        object.__setattr__(self, "fence_token", Id(self.fence_token))
        if isinstance(self.fence_epoch, bool) or self.fence_epoch < 1:
            raise ValueError("writer fence epoch must be positive")

    def to_wire(self) -> dict[str, object]:
        return {
            "lease_id": str(self.lease_id),
            "scope": self.scope.to_wire(),
            "fence_epoch": self.fence_epoch,
            "fence_token": str(self.fence_token),
            "cursor": self.cursor.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class QuiescentJournalBarrier:
    quiescent: bool
    before_digest: Digest
    after_digest: Digest
    cursor: Cursor

    def __post_init__(self) -> None:
        if not self.quiescent or self.before_digest != self.after_digest:
            raise ValueError("writer barrier must be quiescent with matching digests")

    def to_wire(self) -> dict[str, object]:
        return {
            "quiescent": self.quiescent,
            "before_digest": str(self.before_digest),
            "after_digest": str(self.after_digest),
            "cursor": self.cursor.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class CutoverReceipt:
    request_id: Id
    lease: ScopedWriterLease
    journal_digest: Digest
    cursor: Cursor


@dataclass(frozen=True, slots=True)
class GideonRestoreReceipt:
    request_id: Id
    scope: Scope
    prior_lease_id: Id
    prior_fence_epoch: int
    prior_fence_token: Id
    gideon_epoch: int
    cursor: Cursor
    journal_digest: Digest


WriterReconciliation = ScopedWriterLease | CutoverReceipt | GideonRestoreReceipt


@dataclass(frozen=True, slots=True)
class WriterAuthorityStatus:
    scope: Scope
    authority: Literal["gideon", "hypermid_pending", "hypermid"]
    authority_epoch: int
    cursor: Cursor
    active_lease: ScopedWriterLease | None = None
    journal_digest: Digest | None = None


@dataclass(frozen=True, slots=True)
class WriterSnapshot:
    mode: str
    writer: str
    lease_state: str
    lease: ScopedWriterLease | None
    failure: str | None = None
    authority_epoch: int = 1
    restore_receipt: GideonRestoreReceipt | None = None

    @property
    def owns_writes(self) -> bool:
        return (
            self.mode == "primary"
            and self.writer == "hypermid"
            and self.lease_state == "held"
            and self.lease is not None
            and self.authority_epoch == self.lease.fence_epoch
        )


@dataclass(frozen=True, slots=True)
class CutoverBarrierReceipt:
    barrier: QuiescentJournalBarrier
    checkpoints: tuple[ConversationCheckpoint, ...]
    indexed_sessions: int


class WriterLeaseAuthority(Protocol):
    async def acquire(
        self,
        *,
        scope: Scope,
        request_id: Id,
        minimum_fence_epoch: int,
    ) -> ScopedWriterLease: ...

    async def cutover(
        self,
        *,
        scope: Scope,
        request_id: Id,
        lease: ScopedWriterLease,
        barrier: QuiescentJournalBarrier,
    ) -> CutoverReceipt: ...

    async def restore(
        self,
        *,
        scope: Scope,
        request_id: Id,
        lease: ScopedWriterLease,
        barrier: QuiescentJournalBarrier,
    ) -> GideonRestoreReceipt: ...

    async def reconcile(self, request_id: Id) -> WriterReconciliation | None: ...

    async def status(self, *, scope: Scope) -> WriterAuthorityStatus: ...


class CutoverHooks(Protocol):
    async def prepare(self) -> CutoverBarrierReceipt: ...

    async def validate(self, receipt: CutoverBarrierReceipt) -> None: ...

    async def install(self, lease: ScopedWriterLease) -> None: ...

    async def uninstall(self) -> None: ...

    async def resume_primary(self) -> None: ...

    async def resume_gideon(self) -> None: ...


async def _await(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


def _checkpoint_digest(checkpoints: Sequence[ConversationCheckpoint]) -> Digest:
    payload = json.dumps(
        [
            {
                "session_key": checkpoint.session_key,
                "source_digest": checkpoint.source_digest,
                "byte_count": checkpoint.byte_count,
            }
            for checkpoint in checkpoints
        ],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return Digest(hashlib.sha256(payload).hexdigest())


class GideonCutoverHooks:
    """Concrete flush/quiesce/digest barrier over Gideon's current services."""

    def __init__(
        self,
        *,
        log: ConversationLog,
        context: ConversationContextBridge,
        consolidator: object,
        memory_flush: Callable[[], object],
        summary_quiesce: Callable[[], object],
        summary_resume: Callable[[], object],
        install_writer: Callable[[ScopedWriterLease], object],
        uninstall_writer: Callable[[], object],
        session_keys: Callable[[], Sequence[str]],
    ) -> None:
        self.log = log
        self.context = context
        self.consolidator = consolidator
        self.memory_flush = memory_flush
        self.summary_quiesce = summary_quiesce
        self.summary_resume = summary_resume
        self.install_writer = install_writer
        self.uninstall_writer = uninstall_writer
        self.session_keys = session_keys

    async def prepare(self) -> CutoverBarrierReceipt:
        keys = tuple(sorted(set(self.session_keys())))
        try:
            await _await(self.summary_quiesce())
            await quiesce_background_compression(keys or None)
            quiesce = getattr(self.consolidator, "quiesce", None)
            if quiesce is None:
                raise RuntimeError("history consolidator cannot be quiesced")
            await _await(quiesce())
            await _await(self.memory_flush())
            before = self.log.flush_for_hypermid(keys or None)
            indexed = tuple(
                self.context.sync_session(checkpoint.session_key)
                for checkpoint in before
            )
            after = self.log.flush_for_hypermid(keys or None)
            before_digest = _checkpoint_digest(before)
            after_digest = _checkpoint_digest(after)
            cursor = Cursor(1, sum(item.cursor.sequence for item in indexed))
            receipt = CutoverBarrierReceipt(
                QuiescentJournalBarrier(
                    True,
                    before_digest,
                    after_digest,
                    cursor,
                ),
                before,
                len(indexed),
            )
            await self.validate(receipt)
            return receipt
        except BaseException:
            await self.resume_gideon()
            raise

    async def validate(self, receipt: CutoverBarrierReceipt) -> None:
        current = self.log.flush_for_hypermid(
            tuple(checkpoint.session_key for checkpoint in receipt.checkpoints)
        )
        if (
            not self.context.validate_checkpoints(receipt.checkpoints)
            or _checkpoint_digest(current) != receipt.barrier.after_digest
        ):
            raise RuntimeError("ConversationLog source digest changed before cutover")

    async def install(self, lease: ScopedWriterLease) -> None:
        await _await(self.install_writer(lease))

    async def uninstall(self) -> None:
        await _await(self.uninstall_writer())

    async def resume_primary(self) -> None:
        await _await(self.summary_resume())

    async def resume_gideon(self) -> None:
        resume = getattr(self.consolidator, "resume", None)
        if resume is not None:
            await _await(resume())
        resume_background_compression(None)
        await _await(self.summary_resume())


@dataclass(frozen=True, slots=True)
class _PendingOperation:
    kind: Literal["acquire", "cutover", "restore"]
    request_id: Id
    receipt: CutoverBarrierReceipt
    lease: ScopedWriterLease | None = None


class WriterCoordinator:
    """Publish primary ownership only after every cutover precondition holds."""

    def __init__(
        self,
        *,
        mode: str,
        scope: Scope,
        authority: WriterLeaseAuthority,
        hooks: CutoverHooks,
    ) -> None:
        if mode not in {"off", "pass_through", "shadow", "primary"}:
            raise ValueError("unsupported Hypermid mode")
        self.mode = mode
        self.scope = scope
        self.authority = authority
        self.hooks = hooks
        self._lock = asyncio.Lock()
        self._highest_fence_epoch = 1
        self._pending: _PendingOperation | None = None
        self._snapshot = WriterSnapshot(
            mode, "gideon", "none", None, authority_epoch=1
        )

    def snapshot(self) -> WriterSnapshot:
        return self._snapshot

    async def reconcile_startup(self) -> WriterSnapshot:
        """Reconcile durable authority before the runtime can serve a turn."""

        async with self._lock:
            status = await self.authority.status(scope=self.scope)
            if status.scope != self.scope or status.authority_epoch < 1:
                raise RuntimeError("durable writer status is invalid or foreign")
            self._highest_fence_epoch = status.authority_epoch
            if status.authority == "gideon":
                if status.active_lease is not None:
                    raise RuntimeError("Gideon writer status unexpectedly contains a lease")
                self._snapshot = WriterSnapshot(
                    self.mode,
                    "gideon",
                    "none",
                    None,
                    authority_epoch=status.authority_epoch,
                )
                return self._snapshot
            lease = status.active_lease
            if lease is None or lease.scope != self.scope:
                self._snapshot = WriterSnapshot(
                    self.mode,
                    "unknown",
                    "unknown",
                    lease,
                    "durable writer authority has no recoverable active lease",
                    status.authority_epoch,
                )
                raise RuntimeError(self._snapshot.failure)
            self._snapshot = WriterSnapshot(
                self.mode,
                "transition",
                "recovering",
                lease,
                authority_epoch=status.authority_epoch,
            )
            barrier = await self.hooks.prepare()
            await self._restore_pending_lease(barrier, lease)
            return self._snapshot

    async def verify_durable_status(self) -> WriterAuthorityStatus:
        """Fail closed if the local projection diverges from daemon authority."""

        status = await self.authority.status(scope=self.scope)
        snapshot = self._snapshot
        matches = (
            status.scope == self.scope
            and (
                (
                    snapshot.owns_writes
                    and status.authority == "hypermid"
                    and status.active_lease == snapshot.lease
                    and status.authority_epoch == snapshot.authority_epoch
                )
                or (
                    snapshot.writer == "gideon"
                    and snapshot.lease_state == "none"
                    and status.authority == "gideon"
                    and status.active_lease is None
                    and status.authority_epoch == snapshot.authority_epoch
                )
                or snapshot.lease_state in {"unknown", "recovering", "releasing"}
            )
        )
        if not matches:
            self._snapshot = WriterSnapshot(
                self.mode,
                "unknown",
                "unknown",
                status.active_lease,
                "local writer projection diverged from durable authority",
                status.authority_epoch,
            )
            raise RuntimeError(self._snapshot.failure)
        return status

    async def activate_primary(self) -> WriterSnapshot:
        async with self._lock:
            if self.mode != "primary":
                return self._snapshot
            if self._snapshot.owns_writes:
                return self._snapshot
            receipt = await self.hooks.prepare()
            request_id = Id(f"writer:{secrets.token_hex(16)}")
            try:
                lease = await self.authority.acquire(
                    scope=self.scope,
                    request_id=request_id,
                    minimum_fence_epoch=self._highest_fence_epoch + 1,
                )
            except HypermidOutcomeUnknown as error:
                self._pending = _PendingOperation("acquire", request_id, receipt)
                self._snapshot = WriterSnapshot(
                    self.mode,
                    "unknown",
                    "unknown",
                    None,
                    str(error),
                    self._highest_fence_epoch,
                )
                raise
            except BaseException:
                await self.hooks.resume_gideon()
                raise
            if lease.scope != self.scope or lease.fence_epoch <= self._highest_fence_epoch:
                raise RuntimeError("writer authority returned a stale or foreign lease")
            return await self._commit_cutover(receipt, lease)

    async def _commit_cutover(
        self,
        barrier_receipt: CutoverBarrierReceipt,
        lease: ScopedWriterLease,
    ) -> WriterSnapshot:
        request_id = Id(f"writer-cutover:{secrets.token_hex(16)}")
        try:
            cutover = await self.authority.cutover(
                scope=self.scope,
                request_id=request_id,
                lease=lease,
                barrier=barrier_receipt.barrier,
            )
        except HypermidOutcomeUnknown as error:
            self._pending = _PendingOperation(
                "cutover", request_id, barrier_receipt, lease
            )
            self._snapshot = WriterSnapshot(
                self.mode,
                "unknown",
                "unknown",
                lease,
                str(error),
                lease.fence_epoch,
            )
            raise
        except BaseException:
            await self._restore_pending_lease(barrier_receipt, lease)
            raise
        try:
            self._validate_cutover(cutover, request_id, barrier_receipt, lease)
            await self.hooks.validate(barrier_receipt)
            await self.hooks.install(cutover.lease)
            await self.hooks.resume_primary()
        except BaseException:
            await self._restore_pending_lease(barrier_receipt, cutover.lease)
            raise
        self._highest_fence_epoch = cutover.lease.fence_epoch
        self._pending = None
        self._snapshot = WriterSnapshot(
            self.mode,
            "hypermid",
            "held",
            cutover.lease,
            authority_epoch=cutover.lease.fence_epoch,
        )
        return self._snapshot

    async def _restore_pending_lease(
        self,
        barrier_receipt: CutoverBarrierReceipt,
        lease: ScopedWriterLease,
    ) -> GideonRestoreReceipt:
        request_id = Id(f"writer-restore:{secrets.token_hex(16)}")
        try:
            restored = await self.authority.restore(
                scope=self.scope,
                request_id=request_id,
                lease=lease,
                barrier=barrier_receipt.barrier,
            )
        except HypermidOutcomeUnknown as error:
            self._pending = _PendingOperation(
                "restore", request_id, barrier_receipt, lease
            )
            self._snapshot = WriterSnapshot(
                self.mode,
                "unknown",
                "unknown",
                lease,
                str(error),
                lease.fence_epoch,
            )
            raise
        self._validate_restore(restored, request_id, barrier_receipt, lease)
        await self.hooks.uninstall()
        await self.hooks.resume_gideon()
        self._highest_fence_epoch = restored.gideon_epoch
        self._pending = None
        self._snapshot = WriterSnapshot(
            self.mode,
            "gideon",
            "none",
            None,
            authority_epoch=restored.gideon_epoch,
            restore_receipt=restored,
        )
        return restored

    def _validate_cutover(
        self,
        receipt: CutoverReceipt,
        request_id: Id,
        barrier_receipt: CutoverBarrierReceipt,
        pending: ScopedWriterLease,
    ) -> None:
        if (
            receipt.request_id != request_id
            or receipt.lease.scope != self.scope
            or receipt.lease.lease_id != pending.lease_id
            or receipt.lease.fence_epoch != pending.fence_epoch
            or receipt.lease.fence_token != pending.fence_token
            or receipt.cursor != barrier_receipt.barrier.cursor
            or receipt.lease.cursor != receipt.cursor
            or receipt.journal_digest != barrier_receipt.barrier.after_digest
        ):
            raise RuntimeError("writer cutover receipt does not match the prepared barrier")

    def _validate_restore(
        self,
        receipt: GideonRestoreReceipt,
        request_id: Id,
        barrier_receipt: CutoverBarrierReceipt,
        lease: ScopedWriterLease,
    ) -> None:
        if (
            receipt.request_id != request_id
            or receipt.scope != self.scope
            or receipt.prior_lease_id != lease.lease_id
            or receipt.prior_fence_epoch != lease.fence_epoch
            or receipt.prior_fence_token != lease.fence_token
            or receipt.gideon_epoch <= lease.fence_epoch
            or receipt.cursor != barrier_receipt.barrier.cursor
            or receipt.journal_digest != barrier_receipt.barrier.after_digest
        ):
            raise RuntimeError("writer restore receipt does not prove Gideon handback")

    async def reconcile_unknown(self) -> WriterSnapshot:
        async with self._lock:
            pending = self._pending
            if pending is None or self._snapshot.lease_state != "unknown":
                return self._snapshot
            effect = await self.authority.reconcile(pending.request_id)
            if pending.kind == "acquire":
                if effect is None:
                    self._pending = None
                    await self.hooks.resume_gideon()
                    self._snapshot = WriterSnapshot(
                        self.mode,
                        "gideon",
                        "none",
                        None,
                        authority_epoch=self._highest_fence_epoch,
                    )
                    return self._snapshot
                if not isinstance(effect, ScopedWriterLease):
                    raise RuntimeError("writer acquire reconciliation returned wrong effect")
                if effect.scope != self.scope or effect.fence_epoch <= self._highest_fence_epoch:
                    raise RuntimeError("reconciled writer lease is stale or foreign")
                return await self._commit_cutover(pending.receipt, effect)
            if pending.kind == "cutover":
                lease = pending.lease
                if lease is None:
                    raise RuntimeError("cutover reconciliation lost its pending lease")
                if effect is None:
                    await self._restore_pending_lease(pending.receipt, lease)
                    return self._snapshot
                if not isinstance(effect, CutoverReceipt):
                    raise RuntimeError("writer cutover reconciliation returned wrong effect")
                self._validate_cutover(effect, pending.request_id, pending.receipt, lease)
                await self.hooks.validate(pending.receipt)
                await self.hooks.install(effect.lease)
                await self.hooks.resume_primary()
                self._highest_fence_epoch = effect.lease.fence_epoch
                self._pending = None
                self._snapshot = WriterSnapshot(
                    self.mode,
                    "hypermid",
                    "held",
                    effect.lease,
                    authority_epoch=effect.lease.fence_epoch,
                )
                return self._snapshot
            lease = pending.lease
            if lease is None:
                raise RuntimeError("restore reconciliation lost its pending lease")
            if effect is None:
                restored = await self.authority.restore(
                    scope=self.scope,
                    request_id=pending.request_id,
                    lease=lease,
                    barrier=pending.receipt.barrier,
                )
            elif isinstance(effect, GideonRestoreReceipt):
                restored = effect
            else:
                raise RuntimeError("writer restore reconciliation returned wrong effect")
            self._validate_restore(
                restored, pending.request_id, pending.receipt, lease
            )
            await self.hooks.uninstall()
            await self.hooks.resume_gideon()
            self._highest_fence_epoch = restored.gideon_epoch
            self._pending = None
            self._snapshot = WriterSnapshot(
                self.mode,
                "gideon",
                "none",
                None,
                authority_epoch=restored.gideon_epoch,
                restore_receipt=restored,
            )
            return self._snapshot

    async def deactivate(self) -> WriterSnapshot:
        async with self._lock:
            lease = self._snapshot.lease
            if lease is not None:
                self._snapshot = WriterSnapshot(
                    self.mode,
                    "transition",
                    "releasing",
                    lease,
                    authority_epoch=lease.fence_epoch,
                )
                receipt = await self.hooks.prepare()
                await self._restore_pending_lease(receipt, lease)
                return self._snapshot
            elif self._snapshot.lease_state == "unknown":
                raise RuntimeError("unknown writer outcome must be reconciled before fallback")
            await self.hooks.resume_gideon()
            self._snapshot = WriterSnapshot(
                self.mode,
                "gideon",
                "none",
                None,
                authority_epoch=self._highest_fence_epoch,
            )
            return self._snapshot


__all__ = [
    "CutoverReceipt",
    "CutoverBarrierReceipt",
    "GideonRestoreReceipt",
    "GideonCutoverHooks",
    "QuiescentJournalBarrier",
    "ScopedWriterLease",
    "WriterCoordinator",
    "WriterAuthorityStatus",
    "WriterLeaseAuthority",
    "WriterReconciliation",
    "WriterSnapshot",
]
