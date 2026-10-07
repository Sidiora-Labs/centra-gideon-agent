"""MemoryService-compatible access to the authenticated Hypermid memory client."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import secrets
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Iterator,
    Literal,
    Mapping,
    Sequence,
    cast,
)

from gideon.cognition.memory_record import (
    MemoryCapabilities,
)
from gideon.cognition.memory_record import MemoryKind as GideonKind
from gideon.cognition.memory_record import MemoryRecord as GideonRecord
from gideon.cognition.memory_record import MemoryScope as GideonScope
from gideon.cognition.memory_record import (
    MemoryTier,
)
from gideon.integrations.memory_providers.base import MemoryProvider

from .client import HypermidClient, HypermidRemoteError
from .contracts import (
    AccessRequest,
    GrantOperation,
    LineageEdge,
    MemoryOperation,
    MemoryRecord,
    MutationReceipt,
    MutationRequest,
    ProvenanceSpan,
    RecordDraft,
    RecordKind,
    RecordPage,
    RecordStatus,
    RevisionPrecondition,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SourceKind,
    SourceSnapshot,
)
from .embeddings import EmbeddingRegistration
from .foundation import Cursor, Digest, Id, Scope, Trace
from .memory_client import MemoryClient
from .models import JsonValue
from .sources import scope_digest

if TYPE_CHECKING:
    from .writer import WriterCoordinator


_privacy_mode: contextvars.ContextVar[str] = contextvars.ContextVar(
    "hypermid_memory_privacy", default="persistent"
)


def _trace() -> Trace:
    token = secrets.token_hex(16)
    return Trace(Id(f"trace:{token}"), Id(f"request:{token}"))


def _record_id(value: str) -> Id:
    try:
        return Id(value)
    except ValueError:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return Id(f"memory:{digest}")


def _iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


_KIND_TO_HYPERMID = {
    GideonKind.EPISODIC: RecordKind.EPISODE,
    GideonKind.NOTE: RecordKind.NOTE,
}

_RECORD_COLLECTION = Id("memory-records")


class _MemoryClientLoop:
    def __init__(
        self, connection_record: str | Path, scope: Scope, capability_id: Id
    ) -> None:
        self.connection_record = Path(connection_record)
        self.scope = scope
        self.capability_id = capability_id
        self._ready: Future[None] = Future()
        self._thread = threading.Thread(
            target=self._serve, name="hypermid-memory-client", daemon=True
        )
        self._thread.start()
        self._ready.result(timeout=10)

    def _serve(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        client = HypermidClient(self.connection_record, scope=self.scope)
        self.client = MemoryClient(client, capability_id=self.capability_id)
        self._ready.set_result(None)
        self.loop.run_forever()
        self.loop.run_until_complete(client.close())
        self.loop.close()

    def call(
        self, operation: Callable[[MemoryClient], Any], timeout: float = 30.0
    ) -> Any:
        async def invoke() -> Any:
            if not self.client.client.connected:
                await self.client.client.connect()
            return await operation(self.client)

        future = asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=10)


class HypermidMemoryProvider(MemoryProvider):
    """Synchronous provider seam backed only by daemon-owned durable state."""

    name = "hypermid"

    def __init__(
        self,
        connection_record: str | Path,
        *,
        scope: Scope,
        capability_id: Id,
        writer: WriterCoordinator | None = None,
    ) -> None:
        self.scope = scope
        self.capability_id = Id(capability_id)
        self._loop = _MemoryClientLoop(connection_record, scope, self.capability_id)
        self._last_cursor = 0
        self._writer = writer
        self.vector_store = self
        self.embed_fn = None
        self.graph_enabled = True
        self.graph = _RemoteGraph(self)

    def _app_receipt(self):
        from gideon.extensions.apps.app_work import from_bound
        from gideon.extensions.apps.permissions import checker_for
        from gideon.security.session_credentials import current_work

        from .app_scopes import NativeAppScopes

        work = current_work()
        app = from_bound(work)
        if app is None or work is None:
            return None
        checker = checker_for(app.app)
        if (
            checker is None
            or app.current_tier() not in {"read", "tools"}
            or work.memory_mode == "temporary"
        ):
            return None
        if not checker.can_use_memory("app-scoped"):
            return None

        async def resolve(client):
            namespaces = NativeAppScopes(client)
            receipt = await namespaces.lookup(trace=_trace())
            if receipt is None and work.memory_mode == "persistent":
                receipt = await namespaces.issue(trace=_trace())
            return receipt

        return self._loop.call(resolve)

    def _reach(self):
        from gideon.security.session_credentials import memory_reach

        receipt = self._app_receipt()
        return memory_reach(
            receipt.scope if receipt is not None else self.scope,
            require_bound=False,
            app_receipt=receipt,
        )

    def _shared_reads(self) -> bool:
        from gideon.extensions.apps.app_work import from_bound
        from gideon.extensions.apps.permissions import checker_for
        from gideon.security.session_credentials import current_work

        work = current_work()
        app = from_bound(work)
        checker = checker_for(app.app) if app is not None else None
        return bool(
            app is not None
            and app.current_tier() in {"read", "tools"}
            and checker is not None
            and checker.can_use_memory("shared")
        )

    def _target_scope(self) -> Scope:
        receipt = self._app_receipt()
        return (
            receipt.scope
            if receipt is not None and not self._shared_reads()
            else self.scope
        )

    def _read_scopes(self) -> tuple[Scope, ...]:
        receipt = self._app_receipt()
        if receipt is None:
            return (self.scope,)
        return (receipt.scope, self.scope) if self._shared_reads() else (receipt.scope,)

    def _native_record_id(self, value: str, *, target_scope: Scope | None = None) -> Id:
        target = target_scope if target_scope is not None else self._target_scope()
        if isinstance(value, Id):
            return value
        if target == self.scope or value in {
            "memory-records",
            "memory-list",
            "memory-embedding",
        }:
            return _record_id(value)
        return Id(
            "app-memory:"
            + hashlib.sha256(
                str(scope_digest(target)).encode() + b"\0" + value.encode()
            ).hexdigest()
        )

    def _call(
        self,
        operation: Callable[[MemoryClient], Any],
        timeout: float = 30.0,
        *,
        target_scope: Scope | None = None,
    ) -> Any:
        receipt = self._app_receipt()
        target = target_scope if target_scope is not None else self._target_scope()
        if target != self.scope and (receipt is None or target != receipt.scope):
            raise PermissionError("memory target is not a verified native namespace")
        if receipt is not None and target == self.scope and not self._shared_reads():
            raise PermissionError(
                "shared memory consent is required for the owner target"
            )

        async def invoke(client):
            if receipt is not None:
                from .app_scopes import NativeAppScopes

                await NativeAppScopes(client).resolve(receipt, trace=_trace())
                if target == receipt.scope:
                    client = MemoryClient(
                        client.client, capability_id=receipt.capability_id
                    )
            return await operation(client)

        return self._loop.call(invoke, timeout=timeout)

    @contextlib.contextmanager
    def privacy(self, mode: str) -> Iterator[None]:
        if mode not in {"persistent", "incognito", "temporary"}:
            raise ValueError("unsupported memory privacy mode")
        token = _privacy_mode.set(mode)
        try:
            yield
        finally:
            _privacy_mode.reset(token)

    def _reads_allowed(self) -> bool:
        from gideon.security.session_credentials import memory_reach

        return _privacy_mode.get() != "temporary" and self._reach().read_allowed

    def _writes_allowed(self) -> bool:
        from gideon.security.session_credentials import memory_reach

        writer = self._writer
        snapshot = writer.snapshot() if writer is not None else None
        return (
            _privacy_mode.get() == "persistent"
            and self._reach().write_allowed
            and snapshot is not None
            and snapshot.owns_writes
            and snapshot.lease is not None
            and snapshot.lease.scope == self.scope
        )

    def bind_writer(self, writer: WriterCoordinator) -> None:
        if writer.scope != self.scope:
            raise ValueError("memory writer scope does not match provider scope")
        if self._writer not in (None, writer):
            raise RuntimeError("memory provider is already bound to another writer")
        self._writer = writer

    def init(self) -> None:
        health = self._call(lambda client: client.health(trace=_trace()))
        if not health.durable or not health.lexical_available:
            raise RuntimeError("Hypermid memory is not durable and searchable")

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            vector=False,
            transactional_batch=False,
            event_log=True,
            full_text_search=True,
            entity_graph=True,
        )

    def _access(
        self,
        operation: GrantOperation,
        resource_id: str,
        *,
        category: str | None = None,
        target_scope: Scope | None = None,
    ) -> AccessRequest:
        return AccessRequest(
            operation=operation,
            actor_scope=self.scope,
            target_scope=(
                target_scope if target_scope is not None else self._target_scope()
            ),
            resource_id=self._native_record_id(resource_id, target_scope=target_scope),
            trace=_trace(),
            category=category,
        )

    def _mutation(
        self,
        operation: MemoryOperation,
        record_id: str,
        revision: RevisionPrecondition,
        *,
        category: str | None = None,
        native_record_id: bool = False,
    ) -> MutationRequest:
        return MutationRequest(
            operation=operation,
            actor_scope=self.scope,
            target_scope=self._target_scope(),
            revision=revision,
            trace=_trace(),
            record_id=(
                Id(record_id) if native_record_id else self._native_record_id(record_id)
            ),
            category=category,
        )

    def _draft(self, record: GideonRecord) -> RecordDraft:
        metadata = {
            "gideon_id": record.id,
            "gideon_kind": record.kind.value,
            "value": _json_value(record.value),
            "source": record.source,
            "recall_count": record.recall_count,
            "visit_count": record.visit_count,
            "tier": record.tier.value if record.tier else None,
            "scope": record.scope.value,
            "scope_ref": record.scope_ref,
            "conversation_id": record.conversation_id,
            "tags": list(record.tags),
            "safe_to_act": record.safe_to_act,
            "due_window": record.due_window,
            "channel": record.channel,
            "source_ref": record.source_ref,
            **dict(record.extra),
        }
        content = record.text or json.dumps(record.value, ensure_ascii=False)
        content_bytes = content.encode("utf-8")
        content_digest = Digest.sha256(content_bytes)
        locator = str(record.source_ref or record.source or record.id)
        source_identity = Digest.sha256(
            b"hypermid.gideon.memory-source.v2\0"
            + str(scope_digest(self._target_scope())).encode("utf-8")
            + b"\0"
            + str(self._native_record_id(record.id)).encode("utf-8")
            + b"\0"
            + bytes.fromhex(content_digest)
            + b"\0"
            + locator.encode("utf-8")
        )
        source_id = Id(f"gideon-source:{source_identity}")
        return RecordDraft(
            id=self._native_record_id(record.id),
            scope=self._target_scope(),
            kind=_KIND_TO_HYPERMID.get(record.kind, RecordKind.FACT),
            category=record.category or record.kind.value,
            content=content,
            importance=float(record.importance),
            confidence=float(record.confidence),
            metadata=metadata,
            provenance=(
                ProvenanceSpan(
                    source_id=source_id,
                    span_start=0,
                    span_end=len(content_bytes),
                    quoted_digest=content_digest,
                ),
            ),
        )

    def _source(self, draft: RecordDraft, record: GideonRecord) -> SourceSnapshot:
        content = draft.content.encode("utf-8")
        return SourceSnapshot(
            source_id=draft.provenance[0].source_id,
            owner_scope_digest=scope_digest(draft.scope),
            kind=SourceKind.MEMORY,
            source_digest=Digest.sha256(content),
            locator=str(record.source_ref or record.source or record.id),
            captured_content=draft.content,
            capture_method="gideon_memory_record",
            observed_at_ms=time.time_ns() // 1_000_000,
        )

    @staticmethod
    def _gideon(
        record: MemoryRecord, *, native_scope: Scope | None = None
    ) -> GideonRecord:
        metadata = dict(record.current.metadata)
        metadata["native_record_id"] = str(record.id)
        metadata["native_scope_digest"] = str(record.owner_scope_digest)
        if native_scope is not None:
            if scope_digest(native_scope) != record.owner_scope_digest:
                raise PermissionError(
                    "native record scope does not match its authorized source"
                )
            metadata["native_scope"] = native_scope.to_wire()
        kind_value = str(metadata.pop("gideon_kind", ""))
        try:
            kind = GideonKind(kind_value)
        except ValueError:
            kind = (
                GideonKind.EPISODIC
                if record.kind is RecordKind.EPISODE
                else (
                    GideonKind.NOTE
                    if record.kind in {RecordKind.NOTE, RecordKind.SMART_NOTE}
                    else GideonKind.SEMANTIC
                )
            )
        scope_value = str(metadata.pop("scope", GideonScope.GLOBAL.value))
        tier_value = metadata.pop("tier", None)
        return GideonRecord(
            id=str(metadata.pop("gideon_id", record.id)),
            kind=kind,
            text=record.current.content,
            value=metadata.pop("value", record.current.content),
            importance=record.importance,
            confidence=record.confidence,
            source=str(metadata.pop("source", "hypermid")),
            recall_count=int(cast(int | str, metadata.pop("recall_count", 0) or 0)),
            visit_count=int(cast(int | str, metadata.pop("visit_count", 0) or 0)),
            tier=MemoryTier(tier_value) if tier_value else None,
            scope=GideonScope(scope_value),
            scope_ref=cast(str | None, metadata.pop("scope_ref", None)),
            category=record.category,
            conversation_id=str(metadata.pop("conversation_id", "") or ""),
            tags=list(cast(Sequence[str], metadata.pop("tags", ()) or ())),
            is_deleted=record.status.value == "tombstoned",
            created_at=_iso(record.created_at_ms),
            updated_at=_iso(record.updated_at_ms),
            safe_to_act=cast(
                dict[str, JsonValue] | None, metadata.pop("safe_to_act", None)
            ),
            due_window=cast(str | None, metadata.pop("due_window", None)),
            channel=cast(str | None, metadata.pop("channel", None)),
            source_ref=cast(
                dict[str, JsonValue] | None, metadata.pop("source_ref", None)
            ),
            extra=metadata,
        )

    def _remote_get(
        self, record_id: str, *, target_scope: Scope | None = None
    ) -> tuple[MemoryRecord | None, Cursor]:
        request = self._access(
            GrantOperation.READ, record_id, target_scope=target_scope
        )
        return self._call(
            lambda client: client.get(request, authority_resource=_RECORD_COLLECTION),
            target_scope=target_scope,
        )

    def _remote_get_for_upsert(
        self, record_id: str
    ) -> tuple[MemoryRecord | None, object | None]:
        try:
            return self._remote_get(record_id)
        except HypermidRemoteError as error:
            if error.error.code == "SCOPE_NOT_FOUND":
                return None, None
            raise

    def _capture_writer_lease(self) -> dict[str, Any]:
        snapshot = self._writer.snapshot() if self._writer is not None else None
        lease = (
            snapshot.lease if snapshot is not None and snapshot.owns_writes else None
        )
        if lease is None or lease.scope != self.scope:
            raise PermissionError(
                "captured memory requires the active native primary writer"
            )
        return {
            "lease_id": str(lease.lease_id),
            "scope": lease.scope.to_wire(),
            "fence_epoch": lease.fence_epoch,
            "fence_token": str(lease.fence_token),
            "cursor": lease.cursor.to_wire(),
        }

    def put_captured(self, records: list[GideonRecord], capture) -> None:
        from dataclasses import replace

        from gideon.security.capture_origin import validate_capture

        from .contracts import OwnerWordCapture

        verified = validate_capture(capture)
        if verified is None or not self._writes_allowed():
            raise PermissionError("owner-word capture is not currently authorized")
        origin = OwnerWordCapture.from_verified(verified)
        lease = self._capture_writer_lease()
        target = self._target_scope()
        source_id = Id(
            "chat-source:"
            + str(
                Digest.sha256(
                    json.dumps(
                        [
                            target.to_wire(),
                            str(origin.history_session_id),
                            str(origin.source_event_id),
                            str(origin.source_digest),
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
            )
        )
        source = SourceSnapshot(
            source_id=source_id,
            owner_scope_digest=scope_digest(target),
            kind=SourceKind.MESSAGE,
            source_digest=origin.source_digest,
            locator="chat:"
            + str(origin.history_session_id)
            + ":"
            + str(origin.source_event_id),
            captured_content=verified.source_bytes.decode("utf-8"),
            capture_method="accepted_owner_words",
            observed_at_ms=time.time_ns() // 1_000_000,
        )
        for record in records:
            if validate_capture(verified) is None:
                raise PermissionError("owner-word capture expired during publication")
            existing, _ = self._remote_get_for_upsert(record.id)
            draft = self._draft(record)
            metadata = dict(draft.metadata)
            metadata["conversation_id"] = verified.origin_session_key
            draft = replace(
                draft,
                metadata=metadata,
                provenance=(
                    ProvenanceSpan(
                        source_id=source_id,
                        span_start=0,
                        span_end=len(verified.source_bytes),
                        quoted_digest=origin.source_digest,
                    ),
                ),
            )
            operation = (
                MemoryOperation.CREATE if existing is None else MemoryOperation.UPDATE
            )
            revision = (
                RevisionPrecondition.must_not_exist()
                if existing is None
                else RevisionPrecondition.match(str(existing.current.digest))
            )
            request = self._mutation(
                operation, record.id, revision, category=draft.category
            )
            receipt = self._call(
                lambda client: client.write_captured(
                    request,
                    draft,
                    sources=(source,),
                    capture=origin,
                    writer_lease=lease,
                    now_ms=time.time_ns() // 1_000_000,
                )
            )
            self._last_cursor = receipt.cursor.sequence

    def record_capture_origins(self, record_id: str) -> Mapping[str, Any]:
        if not self._reads_allowed():
            raise PermissionError("capture origin inspection is not authorized")
        target = self._target_scope()
        request = self._access(GrantOperation.READ, record_id, target_scope=target)
        return self._call(
            lambda client: client.capture_origins(request), target_scope=target
        )

    def retract_chat_sources(self, history_session_id: Id):
        if not self._writes_allowed():
            raise PermissionError("chat forgetting is not currently authorized")
        lease = self._capture_writer_lease()
        page = cast(RecordPage, self.record_page(limit=1))
        request = self._mutation(
            MemoryOperation.DELETE,
            "memory-records",
            RevisionPrecondition.match(
                str(
                    Digest.sha256(
                        json.dumps(page.cursor.to_wire(), sort_keys=True).encode()
                    )
                )
            ),
            native_record_id=True,
        )
        receipt = self._call(
            lambda client: client.retract_chat(
                request,
                history_session_id=history_session_id,
                expected_cursor=page.cursor,
                writer_lease=lease,
            )
        )
        self._last_cursor = receipt.cursor.sequence
        return receipt

    def put(self, records: list[GideonRecord]) -> None:
        from gideon.security.capture_origin import current_capture

        capture = current_capture()
        if capture is not None:
            self.put_captured(records, capture)
            return
        if not self._writes_allowed():
            return
        now_ms = time.time_ns() // 1_000_000
        for record in records:
            existing, _ = self._remote_get_for_upsert(record.id)
            draft = self._draft(record)
            supersedes = record.extra.get("supersedes")
            if supersedes:
                parent, _ = self._remote_get(str(supersedes))
                if parent is None:
                    raise RuntimeError(
                        "supersession parent is absent from durable memory"
                    )
                draft = RecordDraft(
                    id=draft.id,
                    scope=draft.scope,
                    kind=draft.kind,
                    category=draft.category,
                    content=draft.content,
                    importance=draft.importance,
                    confidence=draft.confidence,
                    expires_at_ms=draft.expires_at_ms,
                    retention_until_ms=draft.retention_until_ms,
                    metadata=draft.metadata,
                    provenance=draft.provenance,
                    lineage=(
                        LineageEdge(
                            parent_id=parent.id,
                            relation="supersedes",
                            parent_revision_digest=parent.current.digest,
                        ),
                    ),
                    summary=draft.summary,
                )
            sources = (self._source(draft, record),)
            if existing is None:
                request = self._mutation(
                    MemoryOperation.CREATE,
                    record.id,
                    RevisionPrecondition.must_not_exist(),
                    category=draft.category,
                )
                receipt = self._call(
                    cast(
                        Callable[[MemoryClient], Awaitable[MutationReceipt]],
                        lambda client, request=request, draft=draft, sources=sources: client.create(
                            request,
                            draft,
                            sources=sources,
                            now_ms=now_ms,
                            authority_resource=_RECORD_COLLECTION,
                        ),
                    )
                )
            else:
                request = self._mutation(
                    MemoryOperation.UPDATE,
                    record.id,
                    RevisionPrecondition.match(str(existing.current.digest)),
                    category=draft.category,
                )
                receipt = self._call(
                    cast(
                        Callable[[MemoryClient], Awaitable[MutationReceipt]],
                        lambda client, request=request, draft=draft, sources=sources: client.update(
                            request,
                            draft,
                            sources=sources,
                            now_ms=now_ms,
                            authority_resource=_RECORD_COLLECTION,
                        ),
                    )
                )
            self._last_cursor = receipt.cursor.sequence

    def get(self, record_id: str) -> GideonRecord | None:
        if not self._reads_allowed():
            return None
        for target in self._read_scopes():
            try:
                record, cursor = self._remote_get(record_id, target_scope=target)
            except HypermidRemoteError as error:
                if error.error.code == "SCOPE_NOT_FOUND":
                    continue
                raise
            self._last_cursor = cursor.sequence
            if record is not None:
                return self._gideon(record, native_scope=target)
        return None

    def delete(self, record_id: str, *, source: str = "user_explicit") -> bool:
        if not self._writes_allowed():
            return False
        record, _ = self._remote_get(record_id)
        if record is None:
            return False
        request = self._mutation(
            MemoryOperation.DELETE,
            record_id,
            RevisionPrecondition.match(str(record.current.digest)),
            category=record.category,
        )
        receipt = self._call(
            lambda client: client.delete(
                request,
                now_ms=time.time_ns() // 1_000_000,
                authority_resource=_RECORD_COLLECTION,
            )
        )
        self._last_cursor = receipt.cursor.sequence
        return True

    def restore(self, record_id: str) -> bool:
        if not self._writes_allowed():
            return False
        record, _ = self._remote_get(record_id)
        if record is None:
            return False
        if record.status is RecordStatus.TOMBSTONED:
            restored = self._gideon(record)
            restored.is_deleted = False
            restored.source = "restore"
            self.put([restored])
            return True
        if record.status is not RecordStatus.ARCHIVED:
            return False
        request = self._mutation(
            MemoryOperation.RESTORE,
            record_id,
            RevisionPrecondition.match(str(record.current.digest)),
            category=record.category,
        )
        receipt = self._call(
            lambda client: client.restore(
                request,
                now_ms=time.time_ns() // 1_000_000,
                authority_resource=_RECORD_COLLECTION,
            )
        )
        self._last_cursor = receipt.cursor.sequence
        return True

    def query(
        self,
        *,
        kinds: set[str] | None = None,
        scope: str | None = None,
        scope_ref: str | None = None,
        include_deleted: bool = False,
        limit: int | None = None,
    ) -> list[GideonRecord]:
        if not self._reads_allowed():
            return []
        native_records: list[tuple[Scope, MemoryRecord]] = []
        for target in self._read_scopes():
            request = self._access(
                GrantOperation.READ, str(_RECORD_COLLECTION), target_scope=target
            )
            try:
                page = self._call(
                    lambda client: client.list(request, limit=min(limit or 1000, 1000)),
                    target_scope=target,
                )
            except HypermidRemoteError as error:
                if error.error.code == "SCOPE_NOT_FOUND":
                    continue
                raise
            self._last_cursor = page.cursor.sequence
            native_records.extend((target, record) for record in page.records)
        records = [
            self._gideon(record, native_scope=target)
            for target, record in native_records
        ]
        if kinds is not None:
            records = [record for record in records if record.kind.value in kinds]
        if scope is not None:
            records = [record for record in records if record.scope.value == scope]
        if scope_ref is not None:
            records = [record for record in records if record.scope_ref == scope_ref]
        if not include_deleted:
            records = [record for record in records if not record.is_deleted]
        return records[:limit] if limit is not None else records

    def record_page(
        self,
        *,
        cursor: Cursor | None = None,
        limit: int = 100,
        category: str | None = None,
        status: RecordStatus | None = None,
        ordered_by_id: bool = False,
        after_id: str | None = None,
    ) -> RecordPage | None:
        if not self._reads_allowed():
            return None
        request = self._access(
            GrantOperation.READ, str(_RECORD_COLLECTION), category=category
        )
        page = self._call(
            lambda client: client.list(
                request,
                cursor=cursor,
                category=category,
                status=status,
                limit=limit,
                ordered_by_id=ordered_by_id,
                after_id=Id(after_id) if after_id is not None else None,
            )
        )
        self._last_cursor = page.cursor.sequence
        return page

    def search_with_evidence(
        self, text: str, *, k: int = 8, mode: SearchMode = SearchMode.HYBRID
    ):
        if not self._reads_allowed() or not text.strip():
            return None
        from .contracts import ScopedSearchResponse

        scopes = self._read_scopes()
        sources = []
        for target in scopes:
            response = self._search_scope(text, target_scope=target, k=k, mode=mode)
            if response is not None:
                sources.append((target, response))
        if not sources:
            return None
        if len(scopes) == 1:
            return sources[0][1]
        return ScopedSearchResponse(tuple(sources), max(1, min(k, 200)))

    def _search_scope(
        self,
        text: str,
        *,
        target_scope: Scope,
        k: int = 8,
        mode: SearchMode = SearchMode.HYBRID,
    ) -> SearchResponse | None:
        if not self._reads_allowed() or not text.strip():
            return None
        access = self._access(
            GrantOperation.SEARCH, str(_RECORD_COLLECTION), target_scope=target_scope
        )
        request = SearchRequest(
            query=text,
            mode=mode,
            limit=max(1, min(k, 200)),
            trace=access.trace,
            now_ms=time.time_ns() // 1_000_000,
        )
        registration_access = (
            self._access(
                GrantOperation.READ, "memory-embedding", target_scope=target_scope
            )
            if mode is not SearchMode.LEXICAL
            else None
        )

        async def search(client):
            if mode is not SearchMode.LEXICAL:
                from dataclasses import replace

                from .providers import (
                    EmbeddingProviderFailure,
                    GideonEmbeddingProviderAuthority,
                    ProviderBinding,
                )

                try:
                    active = (
                        await client.embedding_active(registration_access)
                    ).result.get("registration")
                except HypermidRemoteError as exc:
                    if exc.error.code != "AUTHORIZATION_DENIED":
                        raise
                    active = None
                if active and active["mode"] != "off":
                    authority = GideonEmbeddingProviderAuthority()
                    expected = ProviderBinding(
                        active["provider_identity"],
                        active["model_id"],
                        active["dimensions"],
                    )
                    try:
                        output = await authority.embed(text, expected, access.trace)
                    except EmbeddingProviderFailure:
                        pass
                    else:
                        return await client.search(
                            access,
                            replace(
                                request,
                                query_vector=output.vector,
                                vector_fingerprint=Digest(active["fingerprint"]),
                            ),
                        )
            return await client.search(access, request)

        response = self._call(search, target_scope=target_scope)
        self._last_cursor = response.cursor.sequence
        return response

    def vector_query(
        self,
        *,
        text: str = "",
        embedding: list[float] | None = None,
        k: int = 8,
        kinds: set[str] | None = None,
    ) -> list[dict]:
        if not self._reads_allowed() or not text.strip():
            return []
        response = self.search_with_evidence(
            text,
            k=k,
            mode=SearchMode.HYBRID,
        )
        if response is None:
            return []
        hits = [
            {
                "id": str(hit.id),
                "text": hit.content,
                "score": hit.total_score,
                "kind": hit.kind,
                "source": hit.source.value,
                "content_digest": str(hit.content_digest),
            }
            for hit in response.hits
        ]
        return [hit for hit in hits if kinds is None or hit["kind"] in kinds]

    def lexical_query(self, text: str, *, k: int = 8) -> list[dict]:
        response = self.search_with_evidence(text, k=k, mode=SearchMode.LEXICAL)
        if response is None:
            return []
        return [
            {
                "id": str(hit.id),
                "text": hit.content,
                "score": hit.total_score,
                "kind": hit.kind,
                "source": hit.source.value,
                "content_digest": str(hit.content_digest),
            }
            for hit in response.hits
        ]

    def embed(self, text: str) -> list[float] | None:
        return None

    def _try_embed(self, text: str) -> list[float] | None:
        return self.embed(text)

    def iter_records(
        self, *, kinds: set[str] | None = None, include_deleted: bool = False
    ) -> list[GideonRecord]:
        return self.query(kinds=kinds, include_deleted=include_deleted)

    def get_record(self, record_id: str) -> GideonRecord | None:
        return self.get(record_id)

    def get_all_semantic(self) -> list[dict]:
        rows = self.query(include_deleted=False)
        return [
            {
                "key": row.id,
                "value": row.value,
                "value_json": json.dumps(row.value, ensure_ascii=False),
                "confidence": row.confidence,
                "source": row.source,
                "recall_count": row.recall_count,
                "scope": row.scope.value,
                "scope_ref": row.scope_ref,
                "category": row.category,
            }
            for row in rows
            if row.kind is not GideonKind.EPISODIC
        ]

    def get_semantic(self, key: str) -> dict | None:
        row = self.get(key)
        if row is None or row.kind is GideonKind.EPISODIC:
            return None
        return {
            "key": row.id,
            "value": row.value,
            "value_json": json.dumps(row.value, ensure_ascii=False),
            "confidence": row.confidence,
            "source": row.source,
            "recall_count": row.recall_count,
            "scope": row.scope.value,
            "scope_ref": row.scope_ref,
            "category": row.category,
        }

    def set_semantic(
        self,
        key: str,
        value: object,
        confidence: float,
        source: str,
        *,
        holder: str | None = None,
        weight: float | None = None,
    ) -> None:
        if not self._writes_allowed():
            return None
        remote, _ = self._remote_get_for_upsert(key)
        existing = None if remote is None else self._gideon(remote)
        kind = existing.kind if existing is not None else GideonKind.SEMANTIC
        record = GideonRecord(
            id=key,
            kind=kind,
            value=value,
            confidence=confidence,
            source=source,
            category=existing.category if existing else kind.value,
            scope=existing.scope if existing else GideonScope.GLOBAL,
            scope_ref=existing.scope_ref if existing else None,
            extra={"holder": holder, "weight": weight},
        )
        self.put([record])
        return None

    def delete_semantic(self, key: str, source: str = "user_explicit") -> bool:
        return self.delete(key, source=source)

    def supersede_semantic(self, old_key: str, new_key: str, source: str) -> bool:
        replacement = self.get(new_key)
        old = self.get(old_key)
        if replacement is None or old is None:
            return False
        replacement.extra["supersedes"] = old_key
        replacement.source = source
        self.put([replacement])
        return self.delete(old_key, source="supersede")

    def record_recall(self, keys: list[str]) -> None:
        records = [record for key in keys if (record := self.get(key)) is not None]
        for record in records:
            record.recall_count += 1
            record.source = "recall"
        self.put(records)

    def write_episodic(
        self,
        text: str,
        *,
        embedding: list[float] | None = None,
        conversation_id: str = "",
        tags: list[str] | None = None,
        importance: float = 0.5,
        source: str = "consolidation",
    ) -> bool:
        if not self._writes_allowed():
            return False
        record = GideonRecord(
            id=f"episode:{secrets.token_hex(16)}",
            kind=GideonKind.EPISODIC,
            text=text,
            importance=importance,
            source=source,
            conversation_id=conversation_id,
            tags=list(tags or ()),
            category="episodic",
        )
        self.put([record])
        return True

    def search_episodic(
        self,
        query_text: str,
        *,
        limit: int = 8,
        tags: list[str] | None = None,
        citations_out: list[dict] | None = None,
    ) -> list[dict]:
        hits = self.vector_query(
            text=query_text, k=limit, kinds={GideonKind.EPISODIC.value, "episode"}
        )
        records = []
        for hit in hits:
            record = self.get(str(hit["id"]))
            if record is None or record.kind is not GideonKind.EPISODIC:
                continue
            if tags and not set(tags).issubset(record.tags):
                continue
            row = record.to_public_dict()
            row["score"] = hit["score"]
            records.append(row)
            if citations_out is not None:
                citations_out.append(
                    {"id": record.id, "source": record.source, "score": hit["score"]}
                )
        return records

    def get_episodic_list(
        self, *, limit: int = 50, offset: int = 0, tag_filter: list[str] | None = None
    ) -> list[dict]:
        rows = self.query(kinds={GideonKind.EPISODIC.value}, limit=1000)
        if tag_filter:
            rows = [row for row in rows if set(tag_filter).issubset(row.tags)]
        return [row.to_public_dict() for row in rows[offset : offset + limit]]

    def delete_episodic(self, mem_id: str, *, source: str = "user_explicit") -> bool:
        return self.delete(mem_id, source=source)

    def get_episodic_context(
        self,
        *,
        query_text: str,
        cap: int = 3000,
        citations_out: list[dict] | None = None,
    ) -> str:
        rows = self.search_episodic(query_text, limit=8, citations_out=citations_out)
        return "\n".join(f"- {row['text']}" for row in rows)[:cap]

    def get_semantic_context(self, query_text: str = "", *, cap: int = 1500) -> str:
        if query_text:
            hits = self.vector_query(text=query_text, k=12)
            values = [str(hit["text"]) for hit in hits if hit.get("kind") != "episode"]
        else:
            values = [str(row.get("value", "")) for row in self.get_all_semantic()]
        return "\n".join(f"- {value}" for value in values if value)[:cap]

    def get_l1_manifest(self, cap: int = 800, limit: int = 12) -> str:
        rows = self.query(limit=limit)
        return "\n".join(
            f"- {row.kind.value}:{row.id} ({row.category or 'uncategorized'})"
            for row in rows
        )[:cap]

    def write_lesson(
        self,
        rule: str,
        category: str = "knowledge",
        negative: str | None = None,
        source: str = "user_explicit",
        *,
        scope: GideonScope | None = None,
        scope_ref: str | None = None,
    ) -> bool:
        if not self._writes_allowed():
            return False
        identifier = hashlib.sha256(
            f"{category}\0{rule}\0{negative or ''}".encode("utf-8")
        ).hexdigest()
        self.put(
            [
                GideonRecord(
                    id=f"lesson:{identifier}",
                    kind=GideonKind.LESSON,
                    text=rule,
                    value={"rule": rule, "negative": negative},
                    category=category,
                    source=source,
                    scope=scope or GideonScope.GLOBAL,
                    scope_ref=scope_ref,
                )
            ]
        )
        return True

    def get_lessons(self, limit: int | None = None) -> list[dict]:
        rows = self.query(kinds={GideonKind.LESSON.value}, limit=limit)
        return [row.to_public_dict() for row in rows]

    def lessons_visible_in(
        self, workspace: str | None, *, limit: int | None = None
    ) -> list[dict]:
        rows = self.query(kinds={GideonKind.LESSON.value}, limit=limit)
        visible = [
            row
            for row in rows
            if row.scope is GideonScope.GLOBAL or row.scope_ref == workspace
        ]
        return [row.to_public_dict() for row in visible]

    def get_lessons_context(self, workspace: str | None = None) -> str:
        rows = self.lessons_visible_in(workspace)
        return "\n".join(f"- {row['text']}" for row in rows)

    def lesson_standings(self, rows: list[dict]) -> dict[str, Any]:
        return {
            str(row.get("id") or row.get("key") or index): {
                "accepted": True,
                "support": int(row.get("recall_count", 0) or 0),
                "contradictions": 0,
            }
            for index, row in enumerate(rows)
        }

    def delete_lesson(self, rule_substring: str) -> bool:
        rows = self.query(kinds={GideonKind.LESSON.value})
        match = next((row for row in rows if rule_substring in row.text), None)
        return False if match is None else self.delete(match.id)

    def memory_stats(self) -> dict:
        rows = self.query(include_deleted=True, limit=1000)
        kinds: dict[str, int] = {}
        for row in rows:
            kinds[row.kind.value] = kinds.get(row.kind.value, 0) + 1
        return {
            "total": len(rows),
            "active": sum(not row.is_deleted for row in rows),
            "deleted": sum(row.is_deleted for row in rows),
            "kinds": kinds,
            "cursor": self._last_cursor,
        }

    def get_events(self, limit: int = 50, offset: int = 0) -> list[dict]:
        return self.read_events(limit=limit, offset=offset)

    def undo_event(self, event_id: object) -> tuple[bool, str]:
        event = next(
            (
                row
                for row in self.read_events(limit=1000)
                if str(row.get("id")) == str(event_id)
            ),
            None,
        )
        if event is None:
            return False, "event not found"
        key = str(event.get("memory_key") or "")
        old_value = event.get("old_value")
        if not key:
            return False, "event has no reversible memory key"
        if old_value is None:
            return self.delete(key, source="undo"), "deleted created value"
        self.set_semantic(key, old_value, 1.0, "undo")
        return True, "restored prior value"

    def render_markdown_context(
        self,
        prefs_cap: int = 4_000,
        projects_cap: int = 6_000,
        history_cap: int = 25_000,
    ) -> list[str]:
        del prefs_cap, projects_cap
        semantic = self.get_semantic_context(cap=history_cap // 2)
        episodes = self.get_episodic_list(limit=20)
        episodic = "\n".join(f"- {row['text']}" for row in episodes)[: history_cap // 2]
        blocks = []
        if semantic:
            blocks.append("## Persistent memory\n" + semantic)
        if episodic:
            blocks.append("## Recent episodes\n" + episodic)
        return blocks

    def context_preview(self, query: str = "", *, cap: int = 4000) -> dict:
        semantic = self.get_semantic_context(query, cap=cap // 2)
        episodic = (
            self.get_episodic_context(query_text=query, cap=cap // 2) if query else ""
        )
        text = "\n".join(value for value in (semantic, episodic) if value)[:cap]
        return {"text": text, "cursor": self._last_cursor, "chars": len(text)}

    def embedding_active(self) -> Mapping[str, Any]:
        request = self._access(GrantOperation.READ, "memory-embedding")
        result = self._call(lambda client: client.embedding_active(request))
        return result.result

    def embedding_register(
        self, registration: Mapping[str, Any], *, rebind: bool = False
    ) -> Mapping[str, Any]:
        candidate = EmbeddingRegistration.create(
            registration_id=str(registration.get("registration_id") or ""),
            scope=self._target_scope(),
            mode=cast(
                Literal["off", "local", "remote-compatible", "managed-service"],
                str(registration.get("mode") or "off"),
            ),
            provider_identity=str(registration.get("provider_identity") or ""),
            model_id=str(registration.get("model_id") or ""),
            dimensions=int(registration.get("dimensions") or 0),
            normalized=bool(registration.get("normalized", False)),
        )
        supplied_fingerprint = registration.get("fingerprint")
        if supplied_fingerprint not in (None, candidate.fingerprint):
            raise ValueError(
                "embedding registration fingerprint does not match its fields"
            )
        request = self._mutation(
            MemoryOperation.EMBED,
            candidate.registration_id,
            RevisionPrecondition.must_not_exist(),
            native_record_id=True,
        )
        result = self._call(
            lambda client: client.embedding_register(
                request,
                candidate,
                now_ms=time.time_ns() // 1_000_000,
                rebind=rebind,
            )
        )
        return result.result

    def embedding_retire(self, registration_id: str) -> Mapping[str, Any]:
        request = self._mutation(
            MemoryOperation.EMBED,
            registration_id,
            RevisionPrecondition.must_not_exist(),
            native_record_id=True,
        )
        result = self._call(
            lambda client: client.embedding_retire(
                request,
                registration_id=Id(registration_id),
                now_ms=time.time_ns() // 1_000_000,
            )
        )
        return result.result

    def embedding_publish(
        self, record: MemoryRecord, registration: Mapping[str, Any], response
    ) -> Mapping[str, Any]:
        request = self._mutation(
            MemoryOperation.EMBED,
            str(record.id),
            RevisionPrecondition.match(str(record.current.digest)),
            category=record.category,
            native_record_id=True,
        )
        guard = {
            "record_id": str(record.id),
            "revision_digest": str(record.current.digest),
            "content_digest": str(record.current.content_digest),
            "registration_id": registration["registration_id"],
            "registration_fingerprint": registration["fingerprint"],
        }
        output = {
            "provider_identity": response.provider_identity,
            "model_id": response.model_id,
            "vector": list(response.vector),
            "input_tokens": response.input_tokens,
            "cost_units": response.cost_units,
        }
        result = self._call(
            lambda client: client.embedding_publish(
                request,
                guard=guard,
                response=output,
                now_ms=time.time_ns() // 1_000_000,
            )
        )
        return result.result

    async def embedding_reembed_all(self, *, on_progress=None) -> dict[str, int]:
        """Publish compatible vectors without deleting prior records or vectors."""
        from .providers import GideonEmbeddingProviderAuthority

        authority = GideonEmbeddingProviderAuthority()
        trace = _trace()
        binding = await authority.active_binding(trace)
        view = await asyncio.to_thread(self.embedding_active)
        previous = view.get("registration")
        if not previous or previous.get("mode") == "off":
            raise RuntimeError(
                "Native embedding backfill requires an enabled scoped registration."
            )
        candidate = EmbeddingRegistration.create(
            registration_id=f"embedding:{secrets.token_hex(16)}",
            scope=self._target_scope(),
            mode=previous["mode"],
            provider_identity=binding.provider_identity,
            model_id=binding.model_id,
            dimensions=binding.dimensions,
            normalized=bool(previous["normalized"]),
        )
        if candidate.fingerprint != previous["fingerprint"]:
            await asyncio.to_thread(
                self.embedding_register,
                {
                    "registration_id": candidate.registration_id,
                    "mode": candidate.mode,
                    "provider_identity": candidate.provider_identity,
                    "model_id": candidate.model_id,
                    "dimensions": candidate.dimensions,
                    "normalized": candidate.normalized,
                },
                rebind=True,
            )
            registration = (await asyncio.to_thread(self.embedding_active))[
                "registration"
            ]
        else:
            registration = previous
        done = skipped = total = 0
        seen = {}
        after_id = None
        while True:
            page = await asyncio.to_thread(
                self.record_page, limit=100, after_id=after_id, ordered_by_id=True
            )
            if page is None or not page.records:
                break
            for record in page.records:
                after_id = str(record.id)
                if record.status in {RecordStatus.TOMBSTONED, RecordStatus.STALE}:
                    continue
                seen[str(record.id)] = str(record.current.digest)
                total += 1
                output = await authority.embed(record.current.content, binding, trace)
                try:
                    await asyncio.to_thread(
                        self.embedding_publish, record, registration, output
                    )
                except HypermidRemoteError as exc:
                    if exc.error.code not in {
                        "EMBEDDING_TARGET_UNAVAILABLE",
                        "EMBEDDING_STALE_RESULT",
                    }:
                        raise
                    skipped += 1
                else:
                    done += 1
                if on_progress is not None:
                    on_progress(done, total)
            if len(page.records) < 100:
                break
        current = {}
        after_id = None
        while True:
            page = await asyncio.to_thread(
                self.record_page, limit=100, after_id=after_id, ordered_by_id=True
            )
            if page is None or not page.records:
                break
            for record in page.records:
                after_id = str(record.id)
                if record.status not in {RecordStatus.TOMBSTONED, RecordStatus.STALE}:
                    current[str(record.id)] = str(record.current.digest)
            if len(page.records) < 100:
                break
        changed = {
            key
            for key in seen.keys() | current.keys()
            if seen.get(key) != current.get(key)
        }
        skipped = max(skipped, len(changed))
        if await authority.active_binding(trace) != binding:
            raise RuntimeError(
                "Embedding selection changed during native backfill; retry to complete."
            )
        return {"total": total, "reembedded": done, "skipped": skipped}

    @property
    def alias_index(self) -> dict[str, str]:
        return {
            alias.casefold(): entity.id
            for entity in self.graph.entities()
            for alias in (entity.name, *entity.aliases)
        }

    def invalidate_alias_index(self) -> None:
        return None

    def append_event(
        self,
        *,
        event_type: str,
        memory_type: str,
        memory_key: str,
        old_value: str | None,
        new_value: str | None,
        source: str,
    ) -> int:
        if not self._writes_allowed():
            return 0
        event_id = f"event:{secrets.token_hex(16)}"
        record = GideonRecord(
            id=event_id,
            kind=GideonKind.NOTE,
            text=json.dumps(
                {
                    "event_type": event_type,
                    "memory_type": memory_type,
                    "memory_key": memory_key,
                    "old_value": old_value,
                    "new_value": new_value,
                    "source": source,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            category="audit.event",
            source=source,
        )
        self.put([record])
        return self._last_cursor

    def read_events(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.query(
            kinds={GideonKind.NOTE.value}, include_deleted=True, limit=1000
        )
        events = []
        for record in rows:
            if record.category != "audit.event":
                continue
            try:
                event = json.loads(record.text)
            except json.JSONDecodeError:
                continue
            event.update(id=record.id, created_at=record.created_at)
            events.append(event)
        events.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return events[offset : offset + limit]

    def close(self) -> None:
        self._loop.close()


@dataclass(frozen=True, slots=True)
class _RemoteEntity:
    id: str
    name: str
    entity_type: str
    source: str
    aliases: tuple[str, ...]


class _RemoteGraph:
    def __init__(self, provider: HypermidMemoryProvider) -> None:
        self.provider = provider

    def _records(self, category: str) -> list[GideonRecord]:
        return [
            row
            for row in self.provider.query(
                kinds={GideonKind.NOTE.value}, include_deleted=False, limit=1000
            )
            if row.category == category
        ]

    def entities(self) -> list[_RemoteEntity]:
        result = []
        for row in self._records("graph.entity"):
            try:
                value = json.loads(row.text)
                result.append(
                    _RemoteEntity(
                        row.id,
                        str(value["name"]),
                        str(value["entity_type"]),
                        str(value.get("source", row.source)),
                        tuple(str(item) for item in value.get("aliases", ())),
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        return result

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        *,
        source: str,
        aliases: Sequence[str] | None = None,
    ) -> str:
        existing = next(
            (
                entity
                for entity in self.entities()
                if entity.name.casefold() == name.casefold()
            ),
            None,
        )
        identifier = (
            existing.id
            if existing is not None
            else f"entity:{hashlib.sha256(name.casefold().encode()).hexdigest()}"
        )
        self.provider.put(
            [
                GideonRecord(
                    id=identifier,
                    kind=GideonKind.NOTE,
                    text=json.dumps(
                        {
                            "name": name,
                            "entity_type": entity_type,
                            "source": source,
                            "aliases": list(aliases or ()),
                        },
                        ensure_ascii=False,
                    ),
                    category="graph.entity",
                    source=source,
                )
            ]
        )
        return identifier

    def delete_entity(self, entity_id: str) -> bool:
        return self.provider.delete(entity_id)

    def stats(self, entity_id: str) -> dict:
        links = self.backlinks(entity_id, limit=1000)
        return {"backlinks": len(links)}

    def summary(self) -> dict:
        entities = self.entities()
        links = self._records("graph.link")
        return {"entities": len(entities), "links": len(links)}

    def backlinks(self, entity_id: str, *, limit: int = 100) -> list[dict]:
        result = []
        for row in self._records("graph.link"):
            try:
                value = json.loads(row.text)
            except json.JSONDecodeError:
                continue
            if value.get("to_entity") == entity_id:
                result.append(value)
        return result[:limit]

    def add_link(
        self,
        *,
        from_kind: str,
        from_ref: str,
        link_type: str,
        to_entity: str | None = None,
        to_ref: str | None = None,
        provenance: str = "extracted",
        confidence: float = 1.0,
        context: str | None = None,
        source: str = "linker",
    ) -> bool:
        if bool(to_entity) == bool(to_ref):
            raise ValueError("graph link requires exactly one target")
        value = {
            "from_kind": from_kind,
            "from_ref": from_ref,
            "link_type": link_type,
            "to_entity": to_entity,
            "to_ref": to_ref,
            "provenance": provenance,
            "confidence": float(confidence),
            "context": context,
            "source": source,
        }
        identity = hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        record_id = f"graph-link:{identity}"
        if self.provider.get(record_id) is not None:
            return False
        self.provider.put(
            [
                GideonRecord(
                    id=record_id,
                    kind=GideonKind.NOTE,
                    text=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    category="graph.link",
                    source=source,
                )
            ]
        )
        return self.provider.get(record_id) is not None

    def remove_link(self, link_id: object, *, source: str = "user_explicit") -> bool:
        identifier = str(link_id)
        if not identifier.startswith("graph-link:"):
            identifier = f"graph-link:{identifier}"
        return self.provider.delete(identifier, source=source)

    def drop_links_for(self, from_kind: str, from_ref: str) -> int:
        removed = 0
        for row in self._records("graph.link"):
            try:
                value = json.loads(row.text)
            except json.JSONDecodeError:
                continue
            if (
                value.get("from_kind") == from_kind
                and value.get("from_ref") == from_ref
            ):
                removed += int(self.provider.delete(row.id, source="graph_cleanup"))
        return removed

    def links_from(self, kind: str, record: str) -> list[dict]:
        result = []
        for row in self._records("graph.link"):
            try:
                value = json.loads(row.text)
            except json.JSONDecodeError:
                continue
            if value.get("from_kind") == kind and value.get("from_ref") == record:
                result.append(value)
        return result

    def recall_refs(
        self, text: str, *, index: Mapping[str, str] | None = None, limit: int = 60
    ) -> dict:
        matched = self.resolve_query(text, index=index or self.provider.alias_index)
        weights: dict[str, float] = {}
        for entity_id in matched:
            for link in self.backlinks(entity_id, limit=limit):
                reference = f"{link.get('from_kind', '')}:{link.get('from_ref', '')}"
                weights[reference] = max(
                    weights.get(reference, 0.0),
                    float(link.get("confidence", 0.0) or 0.0),
                )
        return {"entities": matched, "record_boosts": weights}

    def proposals(self) -> list[dict]:
        result = []
        for row in self._records("graph.proposal"):
            try:
                result.append(json.loads(row.text))
            except json.JSONDecodeError:
                continue
        return result

    def accept_proposal(self, name: str, entity_type: str) -> str:
        identity = self.upsert_entity(name, entity_type, source="proposal")
        for row in self._records("graph.proposal"):
            if name.casefold() in row.text.casefold():
                self.provider.delete(row.id)
        return identity

    def reject_proposal(self, name: str) -> bool:
        matched = False
        for row in self._records("graph.proposal"):
            if name.casefold() in row.text.casefold():
                matched = self.provider.delete(row.id) or matched
        return matched

    def entity_graph(self) -> dict:
        nodes = [
            {
                "id": entity.id,
                "name": entity.name,
                "entity_type": entity.entity_type,
                "aliases": list(entity.aliases),
            }
            for entity in self.entities()
        ]
        edges = []
        for row in self._records("graph.link"):
            try:
                edges.append(json.loads(row.text))
            except json.JSONDecodeError:
                continue
        return {"nodes": nodes, "edges": edges}

    def resolve_query(self, text: str, *, index: Mapping[str, str]) -> list[str]:
        folded = text.casefold()
        return list(
            dict.fromkeys(value for key, value in index.items() if key in folded)
        )

    def recall_evidence(self, query_text: str, *, index: Mapping[str, str]) -> dict:
        matches = self.resolve_query(query_text, index=index)
        return {"entities": matches, "count": len(matches)}


def install_as_memory_authority(provider: HypermidMemoryProvider) -> object:
    from gideon.cognition.memory_service import (
        MemoryService,
        install_authoritative_service,
    )

    service = MemoryService(provider)
    install_authoritative_service(service)
    return service


def uninstall_memory_authority(service: object) -> None:
    from gideon.cognition.memory_service import clear_authoritative_service

    clear_authoritative_service(service)


__all__ = [
    "HypermidMemoryProvider",
    "install_as_memory_authority",
    "uninstall_memory_authority",
]
