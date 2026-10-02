from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

from .foundation import Cursor, Digest, Id, Scope, Trace
from .history import SourceAdapter

if TYPE_CHECKING:
    from .client import HypermidClient


CONTEXT_PORTABILITY_SCHEMA_VERSION = 1
MEMORY_PORTABILITY_SCHEMA_VERSION = 2
MAX_PORTABILITY_ENTRIES = 1_000_000
MAX_PORTABILITY_ENTRY_BYTES = 8 * 1024 * 1024
MAX_PORTABILITY_TOTAL_BYTES = 64 * 1024 * 1024


class ContextPortabilityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ContextPortabilityEntryKind(str, Enum):
    SOURCE_REFERENCE = "source_reference"
    SOURCE_SNAPSHOT = "source_snapshot"
    SUMMARY = "summary"
    PROJECTION = "projection"
    POLICY_REVISION = "policy_revision"
    CACHE_GENERATION = "cache_generation"
    REDUCTION = "reduction"


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContextPortabilityError(
            "INVALID_JSON", "portability payload is not canonical JSON"
        ) from exc


def _digest(value: object) -> Digest:
    return Digest(hashlib.sha256(_canonical_bytes(value)).hexdigest())


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextPortabilityError("INVALID_PAYLOAD", f"{field} must be an object")
    return value


def _require_keys(raw: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(raw) != expected:
        raise ContextPortabilityError(
            "INVALID_PAYLOAD", f"{field} fields do not match the canonical schema"
        )


def _contains_forbidden_field(value: object) -> bool:
    forbidden = {
        "credential",
        "credentials",
        "provider_credentials",
        "api_key",
        "access_token",
        "refresh_token",
        "writer_lease",
        "live_lease",
        "transient_queue",
        "sdk_payload",
        "sdk_payloads",
    }
    if isinstance(value, Mapping):
        return any(
            key in forbidden or _contains_forbidden_field(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_field(child) for child in value)
    return False


def _contains_forbidden_memory_field(value: object) -> bool:
    forbidden = (
        "vector",
        "embedding",
        "fts",
        "lease",
        "fencing_token",
        "credential",
        "secret",
        "api_key",
        "access_token",
        "refresh_token",
        "job_claim",
    )
    if isinstance(value, Mapping):
        return any(
            any(term in str(key).lower() for term in forbidden)
            or _contains_forbidden_memory_field(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_memory_field(child) for child in value)
    return False


def _memory_stream_digest(entries: tuple[MemoryExportEntry, ...]) -> Digest:
    digest = hashlib.sha256(b"hypermid.memory.export.v1\0")
    for entry in entries:
        encoded = _canonical_bytes(entry.to_mapping())
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return Digest(digest.hexdigest())


@dataclass(frozen=True, slots=True)
class ContextSessionBinding:
    scope: Scope
    session_id: Id
    cursor: Cursor

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", Id(self.session_id))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_wire(),
            "session_id": str(self.session_id),
            "cursor": self.cursor.to_wire(),
        }

    @classmethod
    def from_mapping(cls, value: object) -> ContextSessionBinding:
        raw = _mapping(value, "binding")
        if set(raw) != {"scope", "session_id", "cursor"}:
            raise ContextPortabilityError(
                "INVALID_BINDING", "session binding fields are incomplete"
            )
        return cls(
            scope=Scope.from_wire(raw["scope"]),
            session_id=Id(raw["session_id"]),
            cursor=Cursor.from_wire(raw["cursor"]),
        )


@dataclass(frozen=True, slots=True)
class ContextPortabilityEntry:
    entry_id: Id
    kind: ContextPortabilityEntryKind
    content_digest: Digest
    byte_length: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", Id(self.entry_id))
        object.__setattr__(self, "kind", ContextPortabilityEntryKind(self.kind))
        object.__setattr__(self, "content_digest", Digest(self.content_digest))
        if (
            isinstance(self.byte_length, bool)
            or not isinstance(self.byte_length, int)
            or not 0 <= self.byte_length <= MAX_PORTABILITY_ENTRY_BYTES
        ):
            raise ContextPortabilityError(
                "ENTRY_TOO_LARGE", "portability entry exceeds the size bound"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "entry_id": str(self.entry_id),
            "kind": self.kind.value,
            "content_digest": str(self.content_digest),
            "byte_length": self.byte_length,
        }

    @classmethod
    def from_mapping(cls, value: object) -> ContextPortabilityEntry:
        raw = _mapping(value, "entry")
        _require_keys(
            raw,
            {"entry_id", "kind", "content_digest", "byte_length"},
            "entry",
        )
        return cls(
            entry_id=Id(raw["entry_id"]),
            kind=ContextPortabilityEntryKind(raw["kind"]),
            content_digest=Digest(raw["content_digest"]),
            byte_length=raw["byte_length"],
        )


@dataclass(frozen=True, slots=True)
class ContextPortabilityManifest:
    manifest_id: Id
    schema_version: int
    scope: Scope
    session_id: Id
    cursor: Cursor
    session_binding_digest: Digest
    entries: tuple[ContextPortabilityEntry, ...]
    entries_digest: Digest
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", Id(self.manifest_id))
        object.__setattr__(self, "session_id", Id(self.session_id))
        object.__setattr__(
            self, "session_binding_digest", Digest(self.session_binding_digest)
        )
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "entries_digest", Digest(self.entries_digest))
        if self.schema_version != CONTEXT_PORTABILITY_SCHEMA_VERSION:
            raise ContextPortabilityError(
                "UNSUPPORTED_VERSION", "context portability schema is unsupported"
            )
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ContextPortabilityError(
                "INVALID_MANIFEST", "manifest created_at is required"
            )
        if len(self.entries) > MAX_PORTABILITY_ENTRIES:
            raise ContextPortabilityError(
                "TOO_MANY_ENTRIES", "manifest contains too many entries"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "manifest_id": str(self.manifest_id),
            "schema_version": self.schema_version,
            "scope": self.scope.to_wire(),
            "session_id": str(self.session_id),
            "cursor": self.cursor.to_wire(),
            "session_binding_digest": str(self.session_binding_digest),
            "entries": [entry.to_mapping() for entry in self.entries],
            "entries_digest": str(self.entries_digest),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, value: object) -> ContextPortabilityManifest:
        raw = _mapping(value, "manifest")
        _require_keys(
            raw,
            {
                "manifest_id",
                "schema_version",
                "scope",
                "session_id",
                "cursor",
                "session_binding_digest",
                "entries",
                "entries_digest",
                "created_at",
            },
            "manifest",
        )
        return cls(
            manifest_id=Id(raw["manifest_id"]),
            schema_version=raw["schema_version"],
            scope=Scope.from_wire(raw["scope"]),
            session_id=Id(raw["session_id"]),
            cursor=Cursor.from_wire(raw["cursor"]),
            session_binding_digest=Digest(raw["session_binding_digest"]),
            entries=tuple(
                ContextPortabilityEntry.from_mapping(entry)
                for entry in raw["entries"]
            ),
            entries_digest=Digest(raw["entries_digest"]),
            created_at=raw["created_at"],
        )


@dataclass(frozen=True, slots=True)
class ContextPortableRecord:
    entry_id: Id
    kind: ContextPortabilityEntryKind
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", Id(self.entry_id))
        object.__setattr__(self, "kind", ContextPortabilityEntryKind(self.kind))
        _mapping(self.payload, "payload")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "entry_id": str(self.entry_id),
            "kind": self.kind.value,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_mapping(cls, value: object) -> ContextPortableRecord:
        raw = _mapping(value, "record")
        _require_keys(raw, {"entry_id", "kind", "payload"}, "record")
        return cls(
            entry_id=Id(raw["entry_id"]),
            kind=ContextPortabilityEntryKind(raw["kind"]),
            payload=_mapping(raw["payload"], "payload"),
        )


@dataclass(frozen=True, slots=True)
class ContextExportBundle:
    binding: ContextSessionBinding
    manifest: ContextPortabilityManifest
    records: tuple[ContextPortableRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "binding": self.binding.to_mapping(),
            "manifest": self.manifest.to_mapping(),
            "records": [record.to_mapping() for record in self.records],
        }

    @classmethod
    def from_mapping(cls, value: object) -> ContextExportBundle:
        raw = _mapping(value, "bundle")
        _require_keys(raw, {"binding", "manifest", "records"}, "bundle")
        return cls(
            binding=ContextSessionBinding.from_mapping(raw["binding"]),
            manifest=ContextPortabilityManifest.from_mapping(raw["manifest"]),
            records=tuple(
                ContextPortableRecord.from_mapping(record) for record in raw["records"]
            ),
        )


def _validate_payload_binding(
    binding: ContextSessionBinding,
    kind: ContextPortabilityEntryKind,
    payload: Mapping[str, Any],
    previous_source_cursor: Cursor | None,
) -> Cursor | None:
    if _contains_forbidden_field(payload):
        raise ContextPortabilityError(
            "FORBIDDEN_STATE",
            "payload contains live, transient, credential, or SDK state",
        )
    try:
        payload_scope = Scope.from_wire(payload["scope"])
        payload_session = Id(payload["session_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContextPortabilityError(
            "INVALID_PAYLOAD", "portability payload is not source bound"
        ) from exc
    if payload_scope != binding.scope or payload_session != binding.session_id:
        raise ContextPortabilityError(
            "SCOPE_MISMATCH", "payload does not match the session binding"
        )
    if kind is not ContextPortabilityEntryKind.SOURCE_REFERENCE:
        return previous_source_cursor
    try:
        Id(payload["item_id"])
        Id(payload["source_event_id"])
        Digest(payload["source_digest"])
        cursor = Cursor.from_wire(payload["cursor"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContextPortabilityError(
            "INVALID_SOURCE_REFERENCE", "source reference is incomplete"
        ) from exc
    if (
        cursor.epoch != binding.cursor.epoch
        or cursor.sequence > binding.cursor.sequence
        or (previous_source_cursor is not None and cursor <= previous_source_cursor)
    ):
        raise ContextPortabilityError(
            "SOURCE_ORDER", "source references are outside strict cursor order"
        )
    return cursor


class ContextExportBuilder:
    def __init__(
        self,
        manifest_id: Id,
        binding: ContextSessionBinding,
        created_at: str,
    ) -> None:
        self._manifest_id = Id(manifest_id)
        self._binding = binding
        if not isinstance(created_at, str) or not created_at:
            raise ContextPortabilityError(
                "INVALID_MANIFEST", "manifest created_at is required"
            )
        self._created_at = created_at
        self._records: list[ContextPortableRecord] = []

    def add(
        self,
        entry_id: Id,
        kind: ContextPortabilityEntryKind,
        payload: Mapping[str, Any],
    ) -> None:
        if len(self._records) >= MAX_PORTABILITY_ENTRIES:
            raise ContextPortabilityError(
                "TOO_MANY_ENTRIES", "manifest contains too many entries"
            )
        record = ContextPortableRecord(entry_id, kind, payload)
        if any(existing.entry_id == record.entry_id for existing in self._records):
            raise ContextPortabilityError(
                "DUPLICATE_ENTRY", "entry identity is duplicated"
            )
        _validate_payload_binding(self._binding, record.kind, record.payload, None)
        if len(_canonical_bytes(record.payload)) > MAX_PORTABILITY_ENTRY_BYTES:
            raise ContextPortabilityError(
                "ENTRY_TOO_LARGE", "portability entry exceeds the size bound"
            )
        self._records.append(record)

    def build(self) -> ContextExportBundle:
        entries: list[ContextPortabilityEntry] = []
        total_bytes = 0
        source_cursor = None
        for record in self._records:
            source_cursor = _validate_payload_binding(
                self._binding, record.kind, record.payload, source_cursor
            )
            payload_bytes = _canonical_bytes(record.payload)
            total_bytes += len(payload_bytes)
            if total_bytes > MAX_PORTABILITY_TOTAL_BYTES:
                raise ContextPortabilityError(
                    "BUNDLE_TOO_LARGE", "portability bundle exceeds the size bound"
                )
            entries.append(
                ContextPortabilityEntry(
                    entry_id=record.entry_id,
                    kind=record.kind,
                    content_digest=Digest(hashlib.sha256(payload_bytes).hexdigest()),
                    byte_length=len(payload_bytes),
                )
            )
        entry_tuple = tuple(entries)
        manifest = ContextPortabilityManifest(
            manifest_id=self._manifest_id,
            schema_version=CONTEXT_PORTABILITY_SCHEMA_VERSION,
            scope=self._binding.scope,
            session_id=self._binding.session_id,
            cursor=self._binding.cursor,
            session_binding_digest=_digest(self._binding.to_mapping()),
            entries=entry_tuple,
            entries_digest=_digest([entry.to_mapping() for entry in entry_tuple]),
            created_at=self._created_at,
        )
        return ContextExportBundle(self._binding, manifest, tuple(self._records))


def validate_context_export(bundle: ContextExportBundle, target_scope: Scope) -> None:
    manifest = bundle.manifest
    if (
        manifest.scope != target_scope
        or bundle.binding.scope != manifest.scope
        or bundle.binding.session_id != manifest.session_id
        or bundle.binding.cursor != manifest.cursor
    ):
        raise ContextPortabilityError(
            "SCOPE_MISMATCH", "manifest does not match the target binding"
        )
    if len(manifest.entries) != len(bundle.records):
        raise ContextPortabilityError(
            "MANIFEST_MISMATCH", "manifest entry count does not match records"
        )
    if manifest.session_binding_digest != _digest(bundle.binding.to_mapping()):
        raise ContextPortabilityError(
            "DIGEST_MISMATCH", "session binding digest does not match"
        )
    if manifest.entries_digest != _digest(
        [entry.to_mapping() for entry in manifest.entries]
    ):
        raise ContextPortabilityError(
            "DIGEST_MISMATCH", "entry manifest digest does not match"
        )
    seen: set[Id] = set()
    total_bytes = 0
    source_cursor = None
    for entry, record in zip(manifest.entries, bundle.records, strict=True):
        if (
            entry.entry_id in seen
            or entry.entry_id != record.entry_id
            or entry.kind is not record.kind
        ):
            raise ContextPortabilityError(
                "MANIFEST_MISMATCH", "manifest entry does not match record"
            )
        seen.add(entry.entry_id)
        source_cursor = _validate_payload_binding(
            bundle.binding, record.kind, record.payload, source_cursor
        )
        payload_bytes = _canonical_bytes(record.payload)
        total_bytes += len(payload_bytes)
        if (
            len(payload_bytes) != entry.byte_length
            or Digest(hashlib.sha256(payload_bytes).hexdigest())
            != entry.content_digest
        ):
            raise ContextPortabilityError(
                "DIGEST_MISMATCH", "record digest does not match"
            )
    if total_bytes > MAX_PORTABILITY_TOTAL_BYTES:
        raise ContextPortabilityError(
            "BUNDLE_TOO_LARGE", "portability bundle exceeds the size bound"
        )


@dataclass(frozen=True, slots=True)
class ContextImportReceipt:
    staging_id: Id
    manifest_id: Id
    scope: Scope
    session_id: Id
    cursor: Cursor
    entries_digest: Digest
    bundle_digest: Digest
    committed: bool


@dataclass(frozen=True, slots=True)
class RestoredContext:
    receipt: ContextImportReceipt
    binding: ContextSessionBinding
    source_references: tuple[ContextPortableRecord, ...]
    derived_records: tuple[ContextPortableRecord, ...]


class ContextImportStore:
    def __init__(self) -> None:
        self._staged: dict[Id, tuple[ContextExportBundle, Digest]] = {}
        self._visible: dict[
            tuple[Scope, Id], tuple[ContextImportReceipt, ContextExportBundle]
        ] = {}
        self._lock = threading.RLock()

    def stage(
        self,
        staging_id: Id,
        target_scope: Scope,
        bundle: ContextExportBundle,
    ) -> Digest:
        staging_id = Id(staging_id)
        validate_context_export(bundle, target_scope)
        bundle_digest = _digest(bundle.to_mapping())
        with self._lock:
            existing = self._staged.get(staging_id)
            if existing is not None:
                if existing[1] != bundle_digest:
                    raise ContextPortabilityError(
                        "STAGING_CONFLICT", "staging identity was reused"
                    )
                return bundle_digest
            self._staged[staging_id] = (bundle, bundle_digest)
            return bundle_digest

    def commit(self, staging_id: Id) -> ContextImportReceipt:
        staging_id = Id(staging_id)
        with self._lock:
            staged = self._staged.get(staging_id)
            if staged is None:
                raise ContextPortabilityError(
                    "STAGE_NOT_FOUND", "staged import was not found"
                )
            bundle, bundle_digest = staged
            key = (bundle.manifest.scope, bundle.manifest.session_id)
            existing = self._visible.get(key)
            if existing is not None:
                if existing[0].bundle_digest != bundle_digest:
                    raise ContextPortabilityError(
                        "VISIBLE_CONFLICT", "another import is already visible"
                    )
                del self._staged[staging_id]
                return existing[0]
            receipt = ContextImportReceipt(
                staging_id=staging_id,
                manifest_id=bundle.manifest.manifest_id,
                scope=bundle.manifest.scope,
                session_id=bundle.manifest.session_id,
                cursor=bundle.manifest.cursor,
                entries_digest=bundle.manifest.entries_digest,
                bundle_digest=bundle_digest,
                committed=True,
            )
            self._visible[key] = (receipt, bundle)
            del self._staged[staging_id]
            return receipt

    def visible(self, scope: Scope, session_id: Id) -> ContextImportReceipt | None:
        with self._lock:
            existing = self._visible.get((scope, Id(session_id)))
            return existing[0] if existing is not None else None

    def restore(
        self, scope: Scope, session_id: Id, source_adapter: SourceAdapter
    ) -> RestoredContext:
        with self._lock:
            existing = self._visible.get((scope, Id(session_id)))
            if existing is None:
                raise ContextPortabilityError(
                    "VISIBLE_NOT_FOUND", "committed import was not found"
                )
            receipt, bundle = existing
            validate_context_export(bundle, scope)
            sources = tuple(
                record
                for record in bundle.records
                if record.kind is ContextPortabilityEntryKind.SOURCE_REFERENCE
            )
            for record in sources:
                try:
                    source_event_id = Id(record.payload["source_event_id"])
                    source_digest = Digest(record.payload["source_digest"])
                    source_bytes = source_adapter.resolve(
                        scope, Id(session_id), source_event_id
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ContextPortabilityError(
                        "SOURCE_UNAVAILABLE",
                        "authoritative source could not be resolved",
                    ) from exc
                if Digest.sha256(source_bytes) != source_digest:
                    raise ContextPortabilityError(
                        "SOURCE_DIGEST_MISMATCH",
                        "authoritative source digest does not match the export",
                    )
            derived = tuple(
                record
                for record in bundle.records
                if record.kind is not ContextPortabilityEntryKind.SOURCE_REFERENCE
            )
            return RestoredContext(receipt, bundle.binding, sources, derived)


class ContextAuthority(str, Enum):
    PREVIOUS = "previous"
    HYPERMID = "hypermid"


@dataclass(frozen=True, slots=True)
class ContextMigrationCheckpoint:
    migration_id: Id
    scope: Scope
    session_id: Id
    committed_cursor: Cursor
    authority: ContextAuthority = ContextAuthority.PREVIOUS

    def __post_init__(self) -> None:
        object.__setattr__(self, "migration_id", Id(self.migration_id))
        object.__setattr__(self, "session_id", Id(self.session_id))
        object.__setattr__(self, "authority", ContextAuthority(self.authority))


class ContextMigrationCoordinator:
    def __init__(self) -> None:
        self._runs: dict[Id, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def begin(
        self,
        migration_id: Id,
        scope: Scope,
        session_id: Id,
        cursor: Cursor,
    ) -> ContextMigrationCheckpoint:
        migration_id = Id(migration_id)
        checkpoint = ContextMigrationCheckpoint(
            migration_id, scope, Id(session_id), cursor
        )
        with self._lock:
            existing = self._runs.get(migration_id)
            if existing is not None:
                current = existing["checkpoint"]
                if (
                    current.scope != scope
                    or current.session_id != checkpoint.session_id
                    or current.committed_cursor.epoch != cursor.epoch
                    or cursor.sequence > current.committed_cursor.sequence
                ):
                    raise ContextPortabilityError(
                        "MIGRATION_CONFLICT", "migration identity was reused"
                    )
                return current
            self._runs[migration_id] = {
                "checkpoint": checkpoint,
                "source_ids": set(),
                "batches": {},
            }
            return checkpoint

    def commit_batch(
        self,
        migration_id: Id,
        batch_id: Id,
        expected_cursor: Cursor,
        next_cursor: Cursor,
        source_event_ids: tuple[Id, ...],
    ) -> ContextMigrationCheckpoint:
        migration_id = Id(migration_id)
        batch_id = Id(batch_id)
        source_event_ids = tuple(Id(value) for value in source_event_ids)
        with self._lock:
            run = self._runs.get(migration_id)
            if run is None:
                raise ContextPortabilityError(
                    "MIGRATION_NOT_FOUND", "migration was not found"
                )
            outcome = (expected_cursor, next_cursor, source_event_ids)
            existing = run["batches"].get(batch_id)
            if existing is not None:
                if existing != outcome:
                    raise ContextPortabilityError(
                        "BATCH_CONFLICT", "migration batch identity was reused"
                    )
                return run["checkpoint"]
            if (
                not source_event_ids
                or expected_cursor != run["checkpoint"].committed_cursor
                or next_cursor.epoch != expected_cursor.epoch
                or next_cursor.sequence
                != expected_cursor.sequence + len(source_event_ids)
            ):
                raise ContextPortabilityError(
                    "INVALID_CURSOR", "migration cursor is not the next range"
                )
            source_set = set(source_event_ids)
            if (
                len(source_set) != len(source_event_ids)
                or source_set & run["source_ids"]
            ):
                raise ContextPortabilityError(
                    "DUPLICATE_SOURCE_IDENTITY", "migration source is duplicated"
                )
            run["source_ids"].update(source_set)
            run["batches"][batch_id] = outcome
            checkpoint = ContextMigrationCheckpoint(
                migration_id,
                run["checkpoint"].scope,
                run["checkpoint"].session_id,
                next_cursor,
                run["checkpoint"].authority,
            )
            run["checkpoint"] = checkpoint
            return checkpoint

    def cutover(
        self, migration_id: Id, *, quiescent: bool, validation_passed: bool
    ) -> ContextMigrationCheckpoint:
        if not quiescent:
            raise ContextPortabilityError(
                "NOT_QUIESCENT", "context authority can change only at a turn boundary"
            )
        if not validation_passed:
            raise ContextPortabilityError(
                "VALIDATION_FAILED", "previous context authority remains active"
            )
        return self._set_authority(migration_id, ContextAuthority.HYPERMID)

    def rollback(
        self, migration_id: Id, *, quiescent: bool
    ) -> ContextMigrationCheckpoint:
        if not quiescent:
            raise ContextPortabilityError(
                "NOT_QUIESCENT", "context authority can change only at a turn boundary"
            )
        return self._set_authority(migration_id, ContextAuthority.PREVIOUS)

    def checkpoint(self, migration_id: Id) -> ContextMigrationCheckpoint:
        with self._lock:
            run = self._runs.get(Id(migration_id))
            if run is None:
                raise ContextPortabilityError(
                    "MIGRATION_NOT_FOUND", "migration was not found"
                )
            return run["checkpoint"]

    def _set_authority(
        self, migration_id: Id, authority: ContextAuthority
    ) -> ContextMigrationCheckpoint:
        migration_id = Id(migration_id)
        with self._lock:
            run = self._runs.get(migration_id)
            if run is None:
                raise ContextPortabilityError(
                    "MIGRATION_NOT_FOUND", "migration was not found"
                )
            current = run["checkpoint"]
            checkpoint = ContextMigrationCheckpoint(
                current.migration_id,
                current.scope,
                current.session_id,
                current.committed_cursor,
                authority,
            )
            run["checkpoint"] = checkpoint
            return checkpoint


@dataclass(frozen=True, slots=True)
class MemoryExportEntry:
    item_key: str
    kind: str
    payload: Mapping[str, Any]
    item_digest: Digest

    def __post_init__(self) -> None:
        if not self.item_key or len(self.item_key) > 512:
            raise ContextPortabilityError("INVALID_ITEM", "memory item key is invalid")
        if not self.kind or len(self.kind) > 64:
            raise ContextPortabilityError("INVALID_ITEM", "memory item kind is invalid")
        object.__setattr__(self, "item_digest", Digest(self.item_digest))
        if Digest.sha256(_canonical_bytes(self.payload)) != self.item_digest:
            raise ContextPortabilityError("DIGEST_MISMATCH", "memory item digest mismatch")
        if _contains_forbidden_memory_field(self.payload):
            raise ContextPortabilityError(
                "FORBIDDEN_STATE", "memory export contains transient or secret-bearing state"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key,
            "kind": self.kind,
            "payload": dict(self.payload),
            "item_digest": str(self.item_digest),
        }

    @classmethod
    def from_mapping(cls, value: object) -> MemoryExportEntry:
        raw = _mapping(value, "memory export entry")
        _require_keys(raw, {"item_key", "kind", "payload", "item_digest"}, "memory export entry")
        return cls(
            str(raw["item_key"]),
            str(raw["kind"]),
            _mapping(raw["payload"], "memory export payload"),
            Digest(raw["item_digest"]),
        )


@dataclass(frozen=True, slots=True)
class MemoryExportManifest:
    export_id: Id
    schema_version: int
    scope: Scope
    record_count: int
    item_count: int
    stream_digest: Digest
    created_at_ms: int
    cursor: Cursor
    trace: Trace
    include_grants: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "export_id", Id(self.export_id))
        object.__setattr__(self, "stream_digest", Digest(self.stream_digest))
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or not 1 <= self.schema_version <= MEMORY_PORTABILITY_SCHEMA_VERSION
            or min(self.record_count, self.item_count, self.created_at_ms) < 0
        ):
            raise ContextPortabilityError("INVALID_MANIFEST", "memory export manifest is invalid")
        if not isinstance(self.include_grants, bool):
            raise ContextPortabilityError("INVALID_MANIFEST", "include_grants must be a boolean")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "export_id": str(self.export_id),
            "schema_version": self.schema_version,
            "scope": self.scope.to_wire(),
            "record_count": self.record_count,
            "item_count": self.item_count,
            "stream_digest": str(self.stream_digest),
            "created_at_ms": self.created_at_ms,
            "cursor": self.cursor.to_wire(),
            "trace": self.trace.to_wire(),
            "include_grants": self.include_grants,
        }

    @classmethod
    def from_mapping(cls, value: object) -> MemoryExportManifest:
        raw = _mapping(value, "memory export manifest")
        _require_keys(raw, {"export_id", "schema_version", "scope", "record_count", "item_count", "stream_digest", "created_at_ms", "cursor", "trace", "include_grants"}, "memory export manifest")
        return cls(
            Id(raw["export_id"]), raw["schema_version"], Scope.from_wire(raw["scope"]),
            raw["record_count"], raw["item_count"], Digest(raw["stream_digest"]),
            raw["created_at_ms"], Cursor.from_wire(raw["cursor"]), Trace.from_wire(raw["trace"]),
            raw["include_grants"],
        )


@dataclass(frozen=True, slots=True)
class MemoryExportBundle:
    manifest: MemoryExportManifest
    entries: tuple[MemoryExportEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        if len(self.entries) != self.manifest.item_count:
            raise ContextPortabilityError("COUNT_MISMATCH", "memory export item count mismatch")
        keys = [entry.item_key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ContextPortabilityError("ITEM_ORDER", "memory export entry keys are not unique")
        if _memory_stream_digest(self.entries) != self.manifest.stream_digest:
            raise ContextPortabilityError("DIGEST_MISMATCH", "memory export stream digest mismatch")
        if sum(entry.kind == "record" for entry in self.entries) != self.manifest.record_count:
            raise ContextPortabilityError("COUNT_MISMATCH", "memory export record count mismatch")

    def to_mapping(self) -> dict[str, Any]:
        return {"manifest": self.manifest.to_mapping(), "entries": [entry.to_mapping() for entry in self.entries]}

    def to_jsonl(self) -> bytes:
        lines = [
            _canonical_bytes({"kind": "manifest", "manifest": self.manifest.to_mapping()})
        ]
        lines.extend(
            _canonical_bytes({"kind": "entry", "entry": entry.to_mapping()})
            for entry in self.entries
        )
        return b"\n".join(lines) + b"\n"

    @classmethod
    def from_mapping(cls, value: object) -> MemoryExportBundle:
        raw = _mapping(value, "memory export bundle")
        _require_keys(raw, {"manifest", "entries"}, "memory export bundle")
        return cls(
            MemoryExportManifest.from_mapping(raw["manifest"]),
            tuple(MemoryExportEntry.from_mapping(entry) for entry in raw["entries"]),
        )

    @classmethod
    def from_jsonl(cls, value: bytes) -> MemoryExportBundle:
        if (
            not isinstance(value, bytes)
            or not value
            or not value.endswith(b"\n")
            or len(value) > MAX_PORTABILITY_TOTAL_BYTES
        ):
            raise ContextPortabilityError("INVALID_JSONL", "memory export JSONL is invalid")
        raw_lines = value.splitlines()
        if not raw_lines or len(raw_lines) > MAX_PORTABILITY_ENTRIES + 1:
            raise ContextPortabilityError("INVALID_JSONL", "memory export JSONL is invalid")
        decoded = []
        for line in raw_lines:
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContextPortabilityError("INVALID_JSONL", "memory export JSONL is invalid") from exc
            if _canonical_bytes(item) != line:
                raise ContextPortabilityError("INVALID_JSONL", "memory export JSONL is not canonical")
            decoded.append(_mapping(item, "memory export JSONL row"))
        _require_keys(decoded[0], {"kind", "manifest"}, "memory export manifest row")
        if decoded[0]["kind"] != "manifest":
            raise ContextPortabilityError("INVALID_JSONL", "memory export manifest row is missing")
        entries = []
        for row in decoded[1:]:
            _require_keys(row, {"kind", "entry"}, "memory export entry row")
            if row["kind"] != "entry":
                raise ContextPortabilityError("INVALID_JSONL", "memory export entry row is invalid")
            entries.append(MemoryExportEntry.from_mapping(row["entry"]))
        return cls(MemoryExportManifest.from_mapping(decoded[0]["manifest"]), tuple(entries))


@dataclass(frozen=True, slots=True)
class MemoryImportManifest:
    schema_version: int
    source_digest: Digest
    source_scope: Scope
    target_scope: Scope
    item_count: int
    scope_mapping: Mapping[str, Scope]
    created_at_ms: int
    trace: Trace

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_digest", Digest(self.source_digest))
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or not 1 <= self.schema_version <= MEMORY_PORTABILITY_SCHEMA_VERSION
            or min(self.item_count, self.created_at_ms) < 0
        ):
            raise ContextPortabilityError("INVALID_MANIFEST", "memory import manifest is invalid")
        mapped: dict[str, Scope] = {}
        for digest, scope in self.scope_mapping.items():
            mapped[str(Digest(digest))] = scope
        object.__setattr__(self, "scope_mapping", mapped)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_digest": str(self.source_digest),
            "source_scope": self.source_scope.to_wire(),
            "target_scope": self.target_scope.to_wire(),
            "item_count": self.item_count,
            "scope_mapping": {key: scope.to_wire() for key, scope in sorted(self.scope_mapping.items())},
            "created_at_ms": self.created_at_ms,
            "trace": self.trace.to_wire(),
        }

    @classmethod
    def from_mapping(cls, value: object) -> MemoryImportManifest:
        raw = _mapping(value, "memory import manifest")
        _require_keys(
            raw,
            {
                "schema_version",
                "source_digest",
                "source_scope",
                "target_scope",
                "item_count",
                "scope_mapping",
                "created_at_ms",
                "trace",
            },
            "memory import manifest",
        )
        mapping = _mapping(raw["scope_mapping"], "memory scope mapping")
        return cls(
            raw["schema_version"],
            Digest(raw["source_digest"]),
            Scope.from_wire(_mapping(raw["source_scope"], "source scope")),
            Scope.from_wire(_mapping(raw["target_scope"], "target scope")),
            raw["item_count"],
            {str(Digest(key)): Scope.from_wire(_mapping(scope, "mapped scope")) for key, scope in mapping.items()},
            raw["created_at_ms"],
            Trace.from_wire(_mapping(raw["trace"], "import trace")),
        )


@dataclass(frozen=True, slots=True)
class MemoryImportBatch:
    batch_id: Id
    manifest: MemoryImportManifest
    state: str
    rejected: tuple[Mapping[str, str], ...]
    replayed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "batch_id", Id(self.batch_id))
        if self.state not in {"staged", "validated", "applied", "rejected"}:
            raise ContextPortabilityError("INVALID_BATCH", "memory import state is invalid")
        if not isinstance(self.replayed, bool):
            raise ContextPortabilityError("INVALID_BATCH", "memory import replay flag is invalid")
        normalized = []
        for value in self.rejected:
            raw = _mapping(value, "memory import rejection")
            _require_keys(raw, {"item_key", "error_code"}, "memory import rejection")
            normalized.append({"item_key": str(raw["item_key"]), "error_code": str(raw["error_code"])})
        object.__setattr__(self, "rejected", tuple(normalized))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "batch_id": str(self.batch_id),
            "manifest": self.manifest.to_mapping(),
            "state": self.state,
            "rejected": [dict(value) for value in self.rejected],
            "replayed": self.replayed,
        }

    @classmethod
    def from_mapping(cls, value: object) -> MemoryImportBatch:
        raw = _mapping(value, "memory import batch")
        _require_keys(raw, {"batch_id", "manifest", "state", "rejected", "replayed"}, "memory import batch")
        rejected = raw["rejected"]
        if not isinstance(rejected, list):
            raise ContextPortabilityError("INVALID_BATCH", "memory import rejections must be a list")
        return cls(
            Id(raw["batch_id"]),
            MemoryImportManifest.from_mapping(raw["manifest"]),
            str(raw["state"]),
            tuple(_mapping(item, "memory import rejection") for item in rejected),
            raw["replayed"],
        )


@dataclass(frozen=True, slots=True)
class LegacyImportReceipt:
    source_digest: Digest
    destination_digest: Digest
    batch: MemoryImportBatch
    record_count: int
    item_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_digest", Digest(self.source_digest))
        object.__setattr__(self, "destination_digest", Digest(self.destination_digest))
        for value in (self.record_count, self.item_count):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContextPortabilityError("INVALID_RECEIPT", "legacy import count is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_digest": str(self.source_digest),
            "destination_digest": str(self.destination_digest),
            "batch": self.batch.to_mapping(),
            "record_count": self.record_count,
            "item_count": self.item_count,
        }

    @classmethod
    def from_mapping(cls, value: object) -> LegacyImportReceipt:
        raw = _mapping(value, "legacy import receipt")
        _require_keys(
            raw,
            {"source_digest", "destination_digest", "batch", "record_count", "item_count"},
            "legacy import receipt",
        )
        return cls(
            Digest(raw["source_digest"]),
            Digest(raw["destination_digest"]),
            MemoryImportBatch.from_mapping(raw["batch"]),
            raw["record_count"],
            raw["item_count"],
        )


class MemoryPortabilityClient:
    def __init__(self, client: HypermidClient) -> None:
        self._client = client

    async def export(self, *, scope: Scope, export_id: Id, include_grants: bool, trace: Trace) -> MemoryExportBundle:
        result = await self._client.request(
            "memory.portability.export",
            {"scope": scope.to_wire(), "export_id": str(export_id), "include_grants": include_grants},
            scope=scope, trace=trace, effect_kind="durable",
        )
        return MemoryExportBundle.from_mapping(result)

    async def stage(self, *, bundle: MemoryExportBundle, target_scope: Scope, scope_mapping: Mapping[str, Scope], batch_id: Id, trace: Trace) -> Mapping[str, Any]:
        return _mapping(await self._client.request(
            "memory.portability.stage",
            {"batch_id": str(batch_id), "bundle": bundle.to_mapping(), "target_scope": target_scope.to_wire(),
             "scope_mapping": {key: scope.to_wire() for key, scope in sorted(scope_mapping.items())}},
            trace=trace, effect_kind="durable",
        ), "memory import batch")

    async def apply(self, *, batch_id: Id, source_digest: Digest, trace: Trace) -> Mapping[str, Any]:
        return _mapping(await self._client.request(
            "memory.portability.apply",
            {"batch_id": str(batch_id), "source_digest": str(source_digest)},
            trace=trace, effect_kind="durable",
        ), "memory import receipt")
