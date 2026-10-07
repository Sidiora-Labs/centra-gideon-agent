from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import MutationRequest
from .foundation import Cursor, Digest, Id, Scope
from .memory_client import MemoryClient
from .migration import (
    ConversationLogEvidence,
    GideonSourceSnapshot,
    KnowledgeCategory,
    MigrationError,
    MigrationStore,
    SourceItem,
    canonical_bytes,
    canonical_digest,
)
from .portability import (
    ContextExportBundle,
    MemoryExportBundle,
    MemoryImportBatch,
    validate_context_export,
)

EXPORT_SCHEMA_VERSION = 1
MAX_EXPORT_BYTES = 512 * 1024 * 1024
EXPORT_EXCLUSIONS = (
    "conversation_log_content",
    "credentials",
    "indexes",
    "jobs",
    "leases",
    "provider_secrets",
    "vectors",
)


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    path: Path
    artifact_digest: Digest
    artifact_bytes: int
    cursor: Cursor
    source_digest: Digest


@dataclass(frozen=True, slots=True)
class MemoryBundleReceipt:
    path: Path
    artifact_digest: Digest
    artifact_bytes: int
    cursor: Cursor
    stream_digest: Digest
    record_count: int
    item_count: int


@dataclass(frozen=True, slots=True)
class MemoryRestoreReceipt:
    batch: MemoryImportBatch
    destination: MemoryExportBundle
    source_stream_digest: Digest
    destination_stream_digest: Digest


@dataclass(frozen=True, slots=True)
class PortableArchive:
    raw: Mapping[str, Any]
    scope: Scope
    cursor: Cursor
    source_digest: Digest
    destination_digest: Digest
    entries: tuple[Mapping[str, Any], ...]
    context_bundles: tuple[ContextExportBundle, ...]

    @classmethod
    def from_mapping(cls, value: object) -> PortableArchive:
        if not isinstance(value, Mapping):
            raise MigrationError("INVALID_EXPORT", "export root must be an object")
        required = {
            "format",
            "schema_version",
            "scope",
            "source_version",
            "cursor",
            "source_digest",
            "destination_digest",
            "entries",
            "conversation_logs",
            "context_bundles",
            "exclusions",
        }
        if not required.issubset(value):
            raise MigrationError("INVALID_EXPORT", "export is missing required fields")
        if (
            value["format"] != "hypermid-portable"
            or value["schema_version"] != EXPORT_SCHEMA_VERSION
        ):
            raise MigrationError("UNSUPPORTED_VERSION", "export schema is unsupported")
        if value["source_version"] != 1:
            raise MigrationError(
                "UNSUPPORTED_VERSION", "export source version is unsupported"
            )
        try:
            scope = Scope.from_wire(value["scope"])
            cursor = Cursor.from_wire(value["cursor"])
            source_digest = Digest(value["source_digest"])
            destination_digest = Digest(value["destination_digest"])
        except (TypeError, ValueError) as exc:
            raise MigrationError(
                "INVALID_EXPORT", "export identity fields are invalid"
            ) from exc
        entries_value = value["entries"]
        bundles_value = value["context_bundles"]
        if not isinstance(entries_value, list) or not isinstance(bundles_value, list):
            raise MigrationError("INVALID_EXPORT", "export collections must be arrays")
        entries = tuple(_entry(entry) for entry in entries_value)
        bundles = tuple(
            ContextExportBundle.from_mapping(bundle) for bundle in bundles_value
        )
        for bundle in bundles:
            validate_context_export(bundle, scope)
        archive = cls(
            dict(value),
            scope,
            cursor,
            source_digest,
            destination_digest,
            entries,
            bundles,
        )
        archive.validate()
        return archive

    def validate(self) -> None:
        if self.cursor.sequence != len(self.entries):
            raise MigrationError(
                "CURSOR_MISMATCH", "export cursor does not cover its entries"
            )
        previous = "0" * 64
        for expected_sequence, entry in enumerate(self.entries, start=1):
            if entry["sequence"] != expected_sequence:
                raise MigrationError(
                    "CURSOR_MISMATCH", "export entries are not in cursor order"
                )
            if entry["previous_digest"] != previous:
                raise MigrationError("CHAIN_MISMATCH", "export digest chain is broken")
            payload = entry["payload"]
            if canonical_digest(payload) != Digest(entry["destination_digest"]):
                raise MigrationError(
                    "DIGEST_MISMATCH", "export destination record changed"
                )
            expected = canonical_digest(
                {
                    "cursor": {
                        "epoch": self.cursor.epoch,
                        "sequence": expected_sequence,
                    },
                    "previous_digest": previous,
                    "destination_digest": entry["destination_digest"],
                }
            )
            if expected != Digest(entry["entry_digest"]):
                raise MigrationError("CHAIN_MISMATCH", "export entry digest is invalid")
            previous = entry["entry_digest"]
        if previous != str(self.destination_digest):
            raise MigrationError("DIGEST_MISMATCH", "export tail digest is invalid")

    def to_mapping(self) -> Mapping[str, Any]:
        return self.raw


def _entry(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MigrationError("INVALID_EXPORT", "export entry must be an object")
    required = {
        "sequence",
        "item_key",
        "category",
        "source_identity",
        "source_digest",
        "destination_digest",
        "payload",
        "import_version",
        "previous_digest",
        "entry_digest",
    }
    if not required.issubset(value):
        raise MigrationError("INVALID_EXPORT", "export entry is incomplete")
    if isinstance(value["sequence"], bool) or not isinstance(value["sequence"], int):
        raise MigrationError("INVALID_EXPORT", "export sequence is invalid")
    try:
        KnowledgeCategory(value["category"])
        Digest(value["source_digest"])
        Digest(value["destination_digest"])
        Digest(value["previous_digest"])
        Digest(value["entry_digest"])
    except (TypeError, ValueError) as exc:
        raise MigrationError(
            "INVALID_EXPORT", "export entry identity is invalid"
        ) from exc
    if not isinstance(value["payload"], Mapping):
        raise MigrationError("INVALID_EXPORT", "export entry payload must be an object")
    return dict(value)


def archive_from_store(
    store: MigrationStore,
    *,
    conversation_logs: Sequence[ConversationLogEvidence] | None = None,
    context_bundles: Sequence[ContextExportBundle] = (),
    unknown: Mapping[str, Any] | None = None,
) -> PortableArchive:
    status = store.status()
    if not status["validation_digest"]:
        raise MigrationError(
            "VALIDATION_REQUIRED", "only a validated store can be exported"
        )
    rows = store.rows_for_export()
    entries: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry["payload"] = json.loads(entry.pop("payload_json"))
        entries.append(entry)
    bundles = tuple(context_bundles)
    for bundle in bundles:
        validate_context_export(bundle, store.scope)
    logs = (
        store.conversation_logs_for_export()
        if conversation_logs is None
        else tuple(conversation_logs)
    )
    if tuple(logs) != store.conversation_logs_for_export():
        raise MigrationError(
            "SOURCE_MISMATCH",
            "export ConversationLog evidence differs from validated store",
        )
    raw: dict[str, Any] = {
        "format": "hypermid-portable",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "scope": store.scope.to_wire(),
        "source_version": 1,
        "cursor": {"epoch": 1, "sequence": len(entries)},
        "source_digest": status["source_digest"],
        "destination_digest": status["destination_digest"],
        "entries": entries,
        "conversation_logs": [entry.to_mapping() for entry in logs],
        "context_bundles": [bundle.to_mapping() for bundle in bundles],
        "exclusions": list(EXPORT_EXCLUSIONS),
    }
    if unknown:
        for key, value in unknown.items():
            if key not in raw:
                raw[key] = value
    return PortableArchive.from_mapping(raw)


def write_export(
    archive: PortableArchive, destination: str | os.PathLike[str]
) -> ExportReceipt:
    archive.validate()
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    if target.exists() and (
        target.is_symlink() or not stat.S_ISREG(target.stat().st_mode)
    ):
        raise MigrationError(
            "INVALID_DESTINATION", "export destination must be a regular file"
        )
    data = canonical_bytes(archive.to_mapping())
    if len(data) > MAX_EXPORT_BYTES:
        raise MigrationError("EXPORT_TOO_LARGE", "export exceeds the size limit")
    from gideon.core.atomic_write import atomic_write_bytes

    atomic_write_bytes(target, data, fsync=True, mode=0o600)
    directory_fd = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return ExportReceipt(
        target, Digest.sha256(data), len(data), archive.cursor, archive.source_digest
    )


def load_export(
    source: str | os.PathLike[str], *, expected_digest: str | None = None
) -> tuple[PortableArchive, ExportReceipt]:
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise MigrationError("INVALID_SOURCE", "export source must be a regular file")
    size = path.stat().st_size
    if size > MAX_EXPORT_BYTES:
        raise MigrationError("EXPORT_TOO_LARGE", "export exceeds the size limit")
    data = path.read_bytes()
    artifact_digest = Digest.sha256(data)
    if expected_digest is not None and artifact_digest != Digest(expected_digest):
        raise MigrationError(
            "ARTIFACT_DIGEST_MISMATCH", "export artifact digest does not match"
        )
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "INVALID_EXPORT", "export is not valid UTF-8 JSON"
        ) from exc
    archive = PortableArchive.from_mapping(value)
    canonical = canonical_bytes(archive.to_mapping())
    if canonical != data:
        raise MigrationError("NON_CANONICAL_EXPORT", "export is not canonical JSON")
    return archive, ExportReceipt(
        path, artifact_digest, len(data), archive.cursor, archive.source_digest
    )


def restore_export(
    archive: PortableArchive,
    target: str | os.PathLike[str],
    *,
    expected_scope: Scope,
) -> MigrationStore:
    if archive.scope != expected_scope:
        raise MigrationError(
            "SCOPE_MISMATCH", "export does not belong to the target scope"
        )
    items: list[SourceItem] = []
    for entry in archive.entries:
        destination = entry["payload"]
        payload = destination.get("payload")
        if not isinstance(payload, Mapping):
            raise MigrationError(
                "INVALID_EXPORT", "destination record has no source payload"
            )
        item = SourceItem.build(
            entry["item_key"],
            KnowledgeCategory(entry["category"]),
            entry["source_identity"],
            payload,
        )
        if item.source_digest != Digest(entry["source_digest"]):
            raise MigrationError(
                "SOURCE_DIGEST_MISMATCH", "restored source item changed"
            )
        items.append(item)
    logs: list[ConversationLogEvidence] = []
    raw_logs = archive.raw["conversation_logs"]
    if not isinstance(raw_logs, list):
        raise MigrationError(
            "INVALID_EXPORT", "conversation log evidence must be an array"
        )
    for entry in raw_logs:
        if not isinstance(entry, Mapping):
            raise MigrationError(
                "INVALID_EXPORT", "conversation log evidence is invalid"
            )
        logs.append(
            ConversationLogEvidence(
                relative_path=str(entry["relative_path"]),
                byte_length=int(entry["byte_length"]),
                source_digest=Digest(entry["source_digest"]),
            )
        )
    snapshot = GideonSourceSnapshot.build(expected_scope, items, logs)
    if snapshot.source_digest != archive.source_digest:
        raise MigrationError(
            "SOURCE_DIGEST_MISMATCH", "restored inventory digest does not match"
        )
    store = MigrationStore(target, expected_scope)
    try:
        receipt = store.import_snapshot(snapshot)
        validation = store.validate(snapshot)
        if (
            receipt.destination_digest != archive.destination_digest
            or validation.destination_digest != archive.destination_digest
        ):
            raise MigrationError(
                "DIGEST_MISMATCH", "restored destination chain differs"
            )
    except BaseException:
        store.close()
        raise
    return store


def write_memory_bundle(
    bundle: MemoryExportBundle, destination: str | os.PathLike[str]
) -> MemoryBundleReceipt:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    if target.exists() and (
        target.is_symlink() or not stat.S_ISREG(target.stat().st_mode)
    ):
        raise MigrationError(
            "INVALID_DESTINATION", "memory export destination is unsafe"
        )
    data = canonical_bytes(bundle.to_mapping())
    if len(data) > MAX_EXPORT_BYTES:
        raise MigrationError("EXPORT_TOO_LARGE", "memory export exceeds the size limit")
    from gideon.core.atomic_write import atomic_write_bytes

    atomic_write_bytes(target, data, fsync=True, mode=0o600)
    directory_fd = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    manifest = bundle.manifest
    return MemoryBundleReceipt(
        target,
        Digest.sha256(data),
        len(data),
        manifest.cursor,
        manifest.stream_digest,
        manifest.record_count,
        manifest.item_count,
    )


def load_memory_bundle(
    source: str | os.PathLike[str], *, expected_digest: str
) -> tuple[MemoryExportBundle, MemoryBundleReceipt]:
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise MigrationError(
            "INVALID_SOURCE", "memory export source must be a regular file"
        )
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise MigrationError("EXPORT_TOO_LARGE", "memory export exceeds the size limit")
    data = path.read_bytes()
    artifact_digest = Digest.sha256(data)
    if artifact_digest != Digest(expected_digest):
        raise MigrationError(
            "ARTIFACT_DIGEST_MISMATCH", "memory export artifact digest changed"
        )
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "INVALID_EXPORT", "memory export is not valid UTF-8 JSON"
        ) from exc
    bundle = MemoryExportBundle.from_mapping(value)
    if canonical_bytes(bundle.to_mapping()) != data:
        raise MigrationError(
            "NON_CANONICAL_EXPORT", "memory export is not canonical JSON"
        )
    manifest = bundle.manifest
    return bundle, MemoryBundleReceipt(
        path,
        artifact_digest,
        len(data),
        manifest.cursor,
        manifest.stream_digest,
        manifest.record_count,
        manifest.item_count,
    )


class RustMemoryPortability:
    """Use the authenticated daemon MemoryApi for export and staged restore."""

    def __init__(self, client: MemoryClient) -> None:
        self.client = client

    async def export_to_file(
        self,
        request: MutationRequest,
        *,
        export_id: str,
        destination: str | os.PathLike[str],
    ) -> MemoryBundleReceipt:
        bundle = await self.client.export_scope(
            request,
            export_id=Id(export_id),
            include_grants=False,
        )
        return write_memory_bundle(bundle, destination)

    async def restore(
        self,
        request: MutationRequest,
        *,
        batch_id: str,
        bundle: MemoryExportBundle,
        target_scope: Scope,
        scope_mapping: Mapping[str, Scope],
        verification_request: MutationRequest,
        verification_export_id: str,
    ) -> MemoryRestoreReceipt:
        if target_scope != bundle.manifest.scope or scope_mapping:
            raise MigrationError(
                "SCOPE_RELOCATION_UNSUPPORTED",
                "this restore path preserves the source scope exactly",
            )
        staged = await self.client.stage_import(
            request,
            batch_id=Id(batch_id),
            bundle=bundle,
            target_scope=target_scope,
            scope_mapping=scope_mapping,
        )
        if staged.state != "validated" or staged.rejected:
            raise MigrationError(
                "IMPORT_REJECTED", "Rust memory import did not validate"
            )
        applied = await self.client.apply_import(request, batch=staged, bundle=bundle)
        if applied.state != "applied" or applied.rejected:
            raise MigrationError(
                "IMPORT_NOT_APPLIED", "Rust memory import did not apply"
            )
        destination = await self.client.export_scope(
            verification_request,
            export_id=Id(verification_export_id),
            include_grants=False,
        )
        source_manifest = bundle.manifest
        target_manifest = destination.manifest
        if (
            target_manifest.stream_digest != source_manifest.stream_digest
            or target_manifest.record_count != source_manifest.record_count
            or target_manifest.item_count != source_manifest.item_count
        ):
            raise MigrationError(
                "DESTINATION_VALIDATION_FAILED",
                "restored Rust memory records differ from the source export",
            )
        return MemoryRestoreReceipt(
            applied,
            destination,
            source_manifest.stream_digest,
            target_manifest.stream_digest,
        )


__all__ = [
    "EXPORT_EXCLUSIONS",
    "ExportReceipt",
    "MemoryBundleReceipt",
    "MemoryRestoreReceipt",
    "PortableArchive",
    "RustMemoryPortability",
    "archive_from_store",
    "load_export",
    "restore_export",
    "load_memory_bundle",
    "write_memory_bundle",
    "write_export",
]
