from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .foundation import Cursor, Digest, EffectState, Scope
from .portability import ContextPortabilityError, MemoryExportBundle

try:
    import fcntl
except ImportError:  # pragma: no cover - SQLite locking remains the non-Unix guard
    fcntl = None


_MAGIC = b"HMBK1\x00"
_FORMAT_VERSION = 1
_CHUNK_BYTES = 1024 * 1024
_MAX_BACKUP_BYTES = 256 * 1024 * 1024
_ZERO_DIGEST = "0" * 64
_FORBIDDEN_FIELDS = frozenset(
    {
        "credential",
        "credentials",
        "api_key",
        "access_token",
        "refresh_token",
        "secret",
        "writer_lease",
        "fencing_token",
        "vector_f32",
        "cache_payload",
    }
)


class LifecycleSecurityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BackupKey:
    _material: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._material, bytes) or len(self._material) != 32:
            raise LifecycleSecurityError(
                "INVALID_BACKUP_KEY", "backup key must contain exactly 32 bytes"
            )

    @classmethod
    def generate(cls) -> BackupKey:
        return cls(secrets.token_bytes(32))

    @classmethod
    def from_base64(cls, value: str) -> BackupKey:
        try:
            material = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as error:
            raise LifecycleSecurityError(
                "INVALID_BACKUP_KEY", "backup key encoding is invalid"
            ) from error
        return cls(material)

    def to_base64(self) -> str:
        return base64.b64encode(self._material).decode("ascii")

    @property
    def key_id(self) -> Digest:
        return Digest.sha256(b"hypermid.backup.key.v1\0" + self._material)


@dataclass(frozen=True, slots=True)
class BackupReceipt:
    scope: Scope
    cursor: Cursor
    source_digest: Digest
    artifact_digest: Digest
    bytes: int


@dataclass(frozen=True, slots=True)
class EncryptedMemoryBundleReceipt:
    export_id: str
    scope: Scope
    cursor: Cursor
    stream_digest: Digest
    source_digest: Digest
    artifact_digest: Digest
    item_count: int
    record_count: int
    bytes: int


@dataclass(frozen=True, slots=True)
class RestoreStageReceipt:
    staging_id: str
    state: str
    scope: Scope
    cursor: Cursor
    source_digest: Digest
    artifact_digest: Digest
    active_digest: Digest
    active_effective_digest: Digest
    active_fingerprint: Digest
    bytes: int
    effect_state: EffectState = EffectState.NOT_STARTED


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    staging_id: str
    state: str
    scope: Scope
    cursor: Cursor
    source_digest: Digest
    artifact_digest: Digest
    active_digest: Digest
    bytes: int
    effect_state: EffectState


@dataclass(slots=True)
class _StagedRestore:
    receipt: RestoreStageReceipt
    active_path: Path
    staging_path: Path


class LifecycleSecurity:
    def __init__(self) -> None:
        self._staged: dict[str, _StagedRestore] = {}

    def export_encrypted(
        self,
        connection: sqlite3.Connection,
        destination: str | os.PathLike[str],
        scope: Scope,
        key: BackupKey,
        *,
        include_grants: bool = False,
    ) -> BackupReceipt:
        snapshot = sqlite3.connect(":memory:")
        try:
            connection.backup(snapshot)
            bundle = _export_bundle(snapshot, scope, include_grants=include_grants)
        finally:
            snapshot.close()
        payload = _canonical_bytes(bundle)
        if len(payload) > _MAX_BACKUP_BYTES:
            raise LifecycleSecurityError("BACKUP_TOO_LARGE", "backup exceeds size limit")
        artifact = _encrypt_bundle(bundle, payload, key)
        destination_path = Path(destination)
        _atomic_private_write(destination_path, artifact)
        manifest = bundle["manifest"]
        return BackupReceipt(
            scope=scope,
            cursor=Cursor.from_wire(manifest["cursor"]),
            source_digest=Digest(manifest["stream_digest"]),
            artifact_digest=Digest.sha256(artifact),
            bytes=len(artifact),
        )

    def stage_restore(
        self,
        active_path: str | os.PathLike[str],
        artifact_path: str | os.PathLike[str],
        expected_scope: Scope,
        key: BackupKey,
        *,
        expected_artifact_digest: Digest,
        supported_schema: int,
    ) -> RestoreStageReceipt:
        active = Path(active_path)
        artifact = Path(artifact_path)
        _require_regular(active, "ACTIVE_STORE_INVALID")
        _require_regular(artifact, "BACKUP_INVALID")
        artifact_bytes = artifact.read_bytes()
        artifact_digest = Digest.sha256(artifact_bytes)
        if artifact_digest != expected_artifact_digest:
            raise LifecycleSecurityError(
                "ARTIFACT_DIGEST_MISMATCH",
                "backup bytes differ from the operator-reviewed artifact",
            )
        bundle = _decrypt_bundle(artifact_bytes, key)
        manifest = _validate_bundle(bundle, expected_scope, supported_schema)
        lease = _acquire_activation_lease(active)
        try:
            _reject_purged_restore(active, bundle, expected_scope)
            active_digest = _file_digest(active)
            active_fingerprint = _sqlite_authority_fingerprint(active)
            parent = active.parent
            descriptor, staging_name = tempfile.mkstemp(
                prefix=".hypermid-secure-restore-", dir=parent
            )
            os.close(descriptor)
            staging_path = Path(staging_name)
            try:
                os.chmod(staging_path, 0o600)
                source = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
                baseline = sqlite3.connect(staging_path)
                try:
                    source.backup(baseline)
                finally:
                    baseline.close()
                    source.close()
                active_effective_digest = _file_digest(staging_path)
                if _sqlite_authority_fingerprint(active) != active_fingerprint:
                    raise LifecycleSecurityError(
                        "ACTIVE_STORE_CHANGED", "active SQLite authority changed during staging"
                    )
                staged = sqlite3.connect(staging_path)
                try:
                    _validate_target_schema(staged, manifest, supported_schema)
                    _replace_scope(staged, bundle, expected_scope)
                    _validate_database_scope(staged, expected_scope, bundle)
                    integrity = staged.execute("PRAGMA integrity_check").fetchone()
                    if integrity != ("ok",):
                        raise LifecycleSecurityError(
                            "RESTORE_INTEGRITY_FAILED", "staged SQLite image is corrupt"
                        )
                finally:
                    staged.close()
                _sync_file(staging_path)
                staging_id = f"restore-{secrets.token_hex(16)}"
                receipt = RestoreStageReceipt(
                    staging_id=staging_id,
                    state="ready",
                    scope=expected_scope,
                    cursor=Cursor.from_wire(manifest["cursor"]),
                    source_digest=Digest(manifest["stream_digest"]),
                    artifact_digest=artifact_digest,
                    active_digest=active_digest,
                    active_effective_digest=active_effective_digest,
                    active_fingerprint=active_fingerprint,
                    bytes=staging_path.stat().st_size,
                )
                self._staged[staging_id] = _StagedRestore(receipt, active, staging_path)
                return receipt
            except BaseException:
                staging_path.unlink(missing_ok=True)
                raise
        finally:
            _release_activation_lease(lease)

    def activate(
        self,
        staging_id: str,
        expected_active_digest: Digest,
        *,
        effect_states: Iterable[EffectState | str] = (),
    ) -> RestoreReceipt:
        _require_no_unknown_effects(effect_states)
        staged = self._staged.get(staging_id)
        if staged is None:
            raise LifecycleSecurityError("STAGING_NOT_FOUND", "restore staging is absent")
        if staged.receipt.active_digest != expected_active_digest:
            raise LifecycleSecurityError(
                "ACTIVE_DIGEST_MISMATCH", "reviewed active digest does not match staging"
            )
        lease = _acquire_activation_lease(staged.active_path)
        try:
            if (
                _sqlite_authority_fingerprint(staged.active_path)
                != staged.receipt.active_fingerprint
                or _effective_sqlite_digest(staged.active_path)
                != staged.receipt.active_effective_digest
            ):
                raise LifecycleSecurityError(
                    "ACTIVE_STORE_CHANGED", "active store changed after validation"
                )
            _checkpoint_inactive_store(staged.active_path)
            os.replace(staged.staging_path, staged.active_path)
            _sync_file(staged.active_path)
            _sync_directory(staged.active_path.parent)
        except OSError as error:
            self._staged.pop(staging_id, None)
            raise LifecycleSecurityError(
                "ACTIVATION_OUTCOME_UNKNOWN",
                "store replacement committed but durability is unknown",
            ) from error
        finally:
            _release_activation_lease(lease)
        self._staged.pop(staging_id, None)
        active_digest = _file_digest(staged.active_path)
        return RestoreReceipt(
            staging_id=staging_id,
            state="committed",
            scope=staged.receipt.scope,
            cursor=staged.receipt.cursor,
            source_digest=staged.receipt.source_digest,
            artifact_digest=staged.receipt.artifact_digest,
            active_digest=active_digest,
            bytes=staged.receipt.bytes,
            effect_state=EffectState.COMMITTED,
        )

    def discard(self, staging_id: str) -> None:
        staged = self._staged.pop(staging_id, None)
        if staged is not None:
            staged.staging_path.unlink(missing_ok=True)


def encrypt_memory_bundle(
    bundle: MemoryExportBundle,
    destination: str | os.PathLike[str],
    key: BackupKey,
) -> EncryptedMemoryBundleReceipt:
    payload = bundle.to_jsonl()
    if len(payload) > _MAX_BACKUP_BYTES:
        raise LifecycleSecurityError("BACKUP_TOO_LARGE", "backup exceeds size limit")
    artifact = _encrypt_payload(bundle.manifest.to_mapping(), payload, key)
    destination_path = Path(destination)
    _atomic_private_write(destination_path, artifact)
    manifest = bundle.manifest
    return EncryptedMemoryBundleReceipt(
        export_id=str(manifest.export_id),
        scope=manifest.scope,
        cursor=manifest.cursor,
        stream_digest=manifest.stream_digest,
        source_digest=Digest.sha256(payload),
        artifact_digest=Digest.sha256(artifact),
        item_count=manifest.item_count,
        record_count=manifest.record_count,
        bytes=len(artifact),
    )


def decrypt_memory_bundle(
    artifact_path: str | os.PathLike[str],
    expected_scope: Scope,
    key: BackupKey,
    *,
    expected_artifact_digest: Digest,
) -> tuple[MemoryExportBundle, EncryptedMemoryBundleReceipt]:
    path = Path(artifact_path)
    _require_regular(path, "BACKUP_INVALID")
    artifact = path.read_bytes()
    artifact_digest = Digest.sha256(artifact)
    if artifact_digest != expected_artifact_digest:
        raise LifecycleSecurityError(
            "ARTIFACT_DIGEST_MISMATCH",
            "backup bytes differ from the operator-reviewed artifact",
        )
    payload, manifest_digest = _decrypt_payload(artifact, key)
    try:
        bundle = MemoryExportBundle.from_jsonl(payload)
    except ContextPortabilityError as error:
        raise LifecycleSecurityError(error.code, str(error)) from error
    manifest = bundle.manifest
    if manifest.scope != expected_scope:
        raise LifecycleSecurityError("SCOPE_MISMATCH", "backup belongs to another scope")
    if not hmac.compare_digest(
        hashlib.sha256(_canonical_bytes(manifest.to_mapping())).hexdigest(),
        manifest_digest,
    ):
        raise LifecycleSecurityError(
            "BACKUP_AUTHENTICATION_FAILED", "encrypted manifest identity differs"
        )
    return bundle, EncryptedMemoryBundleReceipt(
        export_id=str(manifest.export_id),
        scope=manifest.scope,
        cursor=manifest.cursor,
        stream_digest=manifest.stream_digest,
        source_digest=Digest.sha256(payload),
        artifact_digest=artifact_digest,
        item_count=manifest.item_count,
        record_count=manifest.record_count,
        bytes=len(artifact),
    )


def tombstone_record(
    connection: sqlite3.Connection,
    actor_scope: Scope,
    target_scope: Scope,
    record_id: str,
    expected_revision_digest: Digest,
    *,
    now_ms: int,
    retention_until_ms: int,
) -> None:
    if actor_scope != target_scope:
        raise LifecycleSecurityError("AUTHORIZATION_DENIED", "owner authority is required")
    if retention_until_ms <= now_ms:
        raise LifecycleSecurityError("INVALID_RETENTION", "recovery window must be positive")
    scope_digest = _scope_digest(target_scope)
    _ensure_tombstone_table(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT owner_scope_digest, current_revision_digest FROM memory_records WHERE record_id=?",
            (record_id,),
        ).fetchone()
        if row != (scope_digest, str(expected_revision_digest)):
            raise LifecycleSecurityError("STALE_RECORD", "record revision or scope changed")
        connection.execute(
            "UPDATE memory_records SET status='tombstoned', deleted_at_ms=?, retention_until_ms=?, updated_at_ms=? WHERE record_id=?",
            (now_ms, retention_until_ms, now_ms, record_id),
        )
        _remove_derivatives(connection, record_id)
        connection.execute(
            """
            INSERT INTO hypermid_lifecycle_tombstones(
                record_id, owner_scope_digest, revision_digest,
                deleted_at_ms, retention_until_ms, purged_at_ms
            ) VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(record_id) DO UPDATE SET
                owner_scope_digest=excluded.owner_scope_digest,
                revision_digest=excluded.revision_digest,
                deleted_at_ms=excluded.deleted_at_ms,
                retention_until_ms=excluded.retention_until_ms,
                purged_at_ms=NULL
            """,
            (
                record_id,
                scope_digest,
                str(expected_revision_digest),
                now_ms,
                retention_until_ms,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def accept_worker_output(
    connection: sqlite3.Connection,
    target_scope: Scope,
    record_id: str,
    expected_revision_digest: Digest,
) -> None:
    row = connection.execute(
        "SELECT owner_scope_digest, status, current_revision_digest FROM memory_records WHERE record_id=?",
        (record_id,),
    ).fetchone()
    if row != (_scope_digest(target_scope), "active", str(expected_revision_digest)):
        raise LifecycleSecurityError(
            "STALE_WORKER_OUTPUT", "record is no longer eligible for background publication"
        )


def purge_record(
    connection: sqlite3.Connection,
    actor_scope: Scope,
    target_scope: Scope,
    record_id: str,
    *,
    now_ms: int,
) -> None:
    if actor_scope != target_scope:
        raise LifecycleSecurityError("AUTHORIZATION_DENIED", "owner authority is required")
    scope_digest = _scope_digest(target_scope)
    _ensure_tombstone_table(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT status, retention_until_ms, current_revision_digest
            FROM memory_records WHERE record_id=? AND owner_scope_digest=?
            """,
            (record_id, scope_digest),
        ).fetchone()
        if row is None or row[0] != "tombstoned" or row[1] is None or now_ms < row[1]:
            raise LifecycleSecurityError(
                "RETENTION_NOT_ELAPSED", "record is not eligible for physical purge"
            )
        if connection.execute(
            "SELECT EXISTS(SELECT 1 FROM memory_lineage WHERE parent_record_id=?)",
            (record_id,),
        ).fetchone() == (1,):
            raise LifecycleSecurityError("PURGE_REFERENCED", "record remains referenced")
        source_ids = [
            value
            for (value,) in connection.execute(
                "SELECT DISTINCT source_id FROM memory_provenance WHERE record_id=?",
                (record_id,),
            )
        ]
        _remove_derivatives(connection, record_id)
        for table in (
            "memory_verification_events",
            "memory_provenance",
            "memory_lineage",
            "episode_details",
            "smart_note_details",
            "summary_details",
            "memory_revisions",
        ):
            column = "child_record_id" if table == "memory_lineage" else "record_id"
            connection.execute(f"DELETE FROM {table} WHERE {column}=?", (record_id,))
        connection.execute("DELETE FROM memory_records WHERE record_id=?", (record_id,))
        for source_id in source_ids:
            connection.execute(
                "DELETE FROM memory_sources WHERE source_id=? AND NOT EXISTS(SELECT 1 FROM memory_provenance WHERE source_id=?)",
                (source_id, source_id),
            )
        connection.execute(
            "UPDATE hypermid_lifecycle_tombstones SET purged_at_ms=? WHERE record_id=?",
            (now_ms, record_id),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _export_bundle(
    connection: sqlite3.Connection, scope: Scope, *, include_grants: bool
) -> dict[str, Any]:
    _ensure_tombstone_table(connection)
    digest = _scope_digest(scope)
    scope_row = connection.execute(
        "SELECT * FROM memory_scopes WHERE scope_digest=?", (digest,)
    ).fetchone()
    if scope_row is None:
        raise LifecycleSecurityError("SCOPE_NOT_FOUND", "memory scope is absent")
    cursor = Cursor(int(scope_row[2]), int(scope_row[3]))
    schema = connection.execute(
        "SELECT current_version, compatibility_floor, schema_digest FROM hypermid_schema_version WHERE singleton=1"
    ).fetchone()
    if schema is None:
        raise LifecycleSecurityError("SCHEMA_INVALID", "memory schema metadata is absent")
    entries: list[dict[str, Any]] = []
    for table, query, parameters in _export_queries(digest, include_grants):
        for index, row in enumerate(_query_dicts(connection, query, parameters)):
            _reject_forbidden(row)
            payload = {"table": table, "row": row}
            payload_digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            entries.append(
                {
                    "item_key": f"{table}:{index:020d}",
                    "category": table,
                    "source_identity": f"{digest}:{table}",
                    "source_digest": payload_digest,
                    "destination_digest": payload_digest,
                    "payload": payload,
                    "unknown": {},
                }
            )
    entries.sort(key=lambda value: value["item_key"])
    previous = _ZERO_DIGEST
    for entry in entries:
        previous = hashlib.sha256(
            bytes.fromhex(previous)
            + bytes.fromhex(entry["destination_digest"])
            + entry["item_key"].encode("utf-8")
        ).hexdigest()
    manifest = {
        "schema_version": int(schema[0]),
        "compatibility_floor": int(schema[1]),
        "schema_digest": str(schema[2]),
        "scope": scope.to_wire(),
        "source_version": int(schema[0]),
        "cursor": cursor.to_wire(),
        "previous_digest": _ZERO_DIGEST,
        "stream_digest": previous,
        "record_count": sum(
            entry["category"] == "memory_records" for entry in entries
        ),
        "entries": entries,
        "unknown": {},
    }
    bundle = {"format_version": _FORMAT_VERSION, "manifest": manifest, "unknown": {}}
    _validate_bundle(bundle, scope, int(schema[0]))
    return bundle


def _encrypt_bundle(bundle: Mapping[str, Any], payload: bytes, key: BackupKey) -> bytes:
    return _encrypt_payload(bundle["manifest"], payload, key)


def _encrypt_payload(
    manifest: Mapping[str, Any], payload: bytes, key: BackupKey
) -> bytes:
    manifest_digest = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    aes = AESGCM(key._material)
    chunk_records: list[dict[str, Any]] = []
    ciphertext = bytearray()
    chunks = [payload[index : index + _CHUNK_BYTES] for index in range(0, len(payload), _CHUNK_BYTES)]
    for index, chunk in enumerate(chunks):
        nonce = secrets.token_bytes(8) + index.to_bytes(4, "big")
        aad = bytes.fromhex(manifest_digest) + index.to_bytes(4, "big") + len(chunks).to_bytes(4, "big")
        encrypted = aes.encrypt(nonce, chunk, aad)
        chunk_records.append(
            {
                "index": index,
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "bytes": len(encrypted),
                "digest": hashlib.sha256(encrypted).hexdigest(),
            }
        )
        ciphertext.extend(encrypted)
    header = {
        "format_version": _FORMAT_VERSION,
        "key_id": str(key.key_id),
        "manifest_digest": manifest_digest,
        "chunks": chunk_records,
    }
    header_bytes = _canonical_bytes(header)
    return _MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + bytes(ciphertext)


def _decrypt_bundle(artifact: bytes, key: BackupKey) -> Mapping[str, Any]:
    plaintext, manifest_digest = _decrypt_payload(artifact, key)
    try:
        bundle = json.loads(plaintext)
        if not hmac.compare_digest(_canonical_bytes(bundle), plaintext):
            raise ValueError
        decrypted_manifest_digest = hashlib.sha256(
            _canonical_bytes(bundle["manifest"])
        ).hexdigest()
        if not hmac.compare_digest(decrypted_manifest_digest, manifest_digest):
            raise ValueError
        return bundle
    except Exception as error:
        if isinstance(error, LifecycleSecurityError):
            raise
        raise LifecycleSecurityError(
            "BACKUP_AUTHENTICATION_FAILED",
            "backup key, manifest, or encrypted payload is invalid",
        ) from error


def _decrypt_payload(artifact: bytes, key: BackupKey) -> tuple[bytes, str]:
    try:
        if not artifact.startswith(_MAGIC) or len(artifact) < len(_MAGIC) + 4:
            raise ValueError
        header_length = struct.unpack(">I", artifact[len(_MAGIC) : len(_MAGIC) + 4])[0]
        header_start = len(_MAGIC) + 4
        header_end = header_start + header_length
        if header_end > len(artifact):
            raise ValueError
        header = json.loads(artifact[header_start:header_end])
        if header["key_id"] != str(key.key_id):
            raise ValueError
        manifest_digest = header["manifest_digest"]
        offset = header_end
        aes = AESGCM(key._material)
        plaintext = bytearray()
        chunks = header["chunks"]
        for index, chunk in enumerate(chunks):
            if chunk["index"] != index:
                raise ValueError
            count = int(chunk["bytes"])
            encrypted = artifact[offset : offset + count]
            offset += count
            if len(encrypted) != count or not hmac.compare_digest(
                hashlib.sha256(encrypted).hexdigest(), chunk["digest"]
            ):
                raise ValueError
            nonce = base64.b64decode(chunk["nonce"], validate=True)
            aad = (
                bytes.fromhex(manifest_digest)
                + index.to_bytes(4, "big")
                + len(chunks).to_bytes(4, "big")
            )
            plaintext.extend(aes.decrypt(nonce, encrypted, aad))
        if offset != len(artifact) or len(plaintext) > _MAX_BACKUP_BYTES:
            raise ValueError
        return bytes(plaintext), manifest_digest
    except Exception as error:
        if isinstance(error, LifecycleSecurityError):
            raise
        raise LifecycleSecurityError(
            "BACKUP_AUTHENTICATION_FAILED",
            "backup key, manifest, or encrypted payload is invalid",
        ) from error


def _validate_bundle(
    bundle: Mapping[str, Any], expected_scope: Scope, supported_schema: int
) -> Mapping[str, Any]:
    try:
        if set(bundle) != {"format_version", "manifest", "unknown"} or bundle["format_version"] != 1:
            raise ValueError
        manifest = bundle["manifest"]
        if Scope.from_wire(manifest["scope"]) != expected_scope:
            raise LifecycleSecurityError("SCOPE_MISMATCH", "backup belongs to another scope")
        if int(manifest["schema_version"]) > supported_schema:
            raise LifecycleSecurityError("STORE_AHEAD", "backup schema is newer than this binary")
        Cursor.from_wire(manifest["cursor"])
        previous = manifest["previous_digest"]
        if previous != _ZERO_DIGEST:
            raise ValueError
        seen: set[str] = set()
        item_keys: list[str] = []
        for entry in manifest["entries"]:
            if (
                entry["item_key"] in seen
                or entry["unknown"] != {}
                or entry["category"] != entry["payload"]["table"]
                or entry["source_identity"]
                != f"{_scope_digest(expected_scope)}:{entry['category']}"
            ):
                raise ValueError
            seen.add(entry["item_key"])
            item_keys.append(entry["item_key"])
            payload = entry["payload"]
            _reject_forbidden(payload)
            digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            if digest != entry["source_digest"] or digest != entry["destination_digest"]:
                raise ValueError
            previous = hashlib.sha256(
                bytes.fromhex(previous)
                + bytes.fromhex(digest)
                + entry["item_key"].encode("utf-8")
            ).hexdigest()
        if previous != manifest["stream_digest"]:
            raise ValueError
        if item_keys != sorted(item_keys) or manifest["unknown"] != {}:
            raise ValueError
        if int(manifest["source_version"]) != int(manifest["schema_version"]):
            raise ValueError
        if int(manifest["compatibility_floor"]) > int(manifest["schema_version"]):
            raise ValueError
        if int(manifest["record_count"]) != sum(
            entry["category"] == "memory_records" for entry in manifest["entries"]
        ):
            raise ValueError
        _validate_memory_invariants(manifest, expected_scope)
        return manifest
    except LifecycleSecurityError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise LifecycleSecurityError(
            "BACKUP_MANIFEST_INVALID", "backup manifest invariants failed"
        ) from error


def _validate_memory_invariants(manifest: Mapping[str, Any], scope: Scope) -> None:
    digest = _scope_digest(scope)
    rows: dict[str, list[Mapping[str, Any]]] = {}
    for entry in manifest["entries"]:
        payload = entry["payload"]
        rows.setdefault(payload["table"], []).append(payload["row"])
    scopes = rows.get("memory_scopes", [])
    if len(scopes) != 1 or scopes[0]["scope_digest"] != digest:
        raise LifecycleSecurityError("SCOPE_MISMATCH", "scope row is invalid")
    if json.loads(scopes[0]["scope_json"]) != scope.to_wire():
        raise LifecycleSecurityError("SCOPE_MISMATCH", "scope identity is invalid")
    if Cursor.from_wire(manifest["cursor"]) != Cursor(
        int(scopes[0]["epoch"]), int(scopes[0]["sequence"])
    ):
        raise LifecycleSecurityError("CURSOR_INVALID", "manifest cursor differs from scope")
    records = {row["record_id"]: row for row in rows.get("memory_records", [])}
    revisions: dict[str, list[Mapping[str, Any]]] = {}
    for revision in rows.get("memory_revisions", []):
        if hashlib.sha256(revision["content"].encode()).hexdigest() != revision["content_digest"]:
            raise LifecycleSecurityError("DIGEST_MISMATCH", "revision content digest differs")
        if _revision_digest(revision) != revision["revision_digest"]:
            raise LifecycleSecurityError("DIGEST_MISMATCH", "revision digest differs")
        revisions.setdefault(revision["record_id"], []).append(revision)
    for record_id, record in records.items():
        if record["owner_scope_digest"] != digest:
            raise LifecycleSecurityError("SCOPE_MISMATCH", "record scope differs")
        chain = sorted(revisions.get(record_id, []), key=lambda row: row["revision"])
        if [row["revision"] for row in chain] != list(range(1, len(chain) + 1)):
            raise LifecycleSecurityError("REVISION_CHAIN_INVALID", "revision chain is not contiguous")
        if not chain or chain[-1]["revision"] != record["current_revision"] or chain[-1]["revision_digest"] != record["current_revision_digest"]:
            raise LifecycleSecurityError("REVISION_CHAIN_INVALID", "current revision is absent")
        for index, revision in enumerate(chain):
            expected_parent = None if index == 0 else chain[index - 1]["revision_digest"]
            if revision["parent_revision_digest"] != expected_parent:
                raise LifecycleSecurityError("REVISION_CHAIN_INVALID", "revision parent differs")
        if record["kind"] == "anchor" and len(chain) != 1:
            raise LifecycleSecurityError("ANCHOR_INVALID", "anchor has multiple revisions")
        normalized = " ".join(chain[-1]["content"].split()).lower()
        if hashlib.sha256(normalized.encode()).hexdigest() != record["normalized_content_digest"]:
            raise LifecycleSecurityError("DIGEST_MISMATCH", "normalized content digest differs")
        tombstoned = record["status"] == "tombstoned"
        if tombstoned != (record["deleted_at_ms"] is not None):
            raise LifecycleSecurityError("TOMBSTONE_INVALID", "tombstone timestamps differ")
        if tombstoned and (
            record["retention_until_ms"] is None
            or record["retention_until_ms"] <= record["deleted_at_ms"]
        ):
            raise LifecycleSecurityError("TOMBSTONE_INVALID", "recovery retention is invalid")
    sources = {row["source_id"]: row for row in rows.get("memory_sources", [])}
    for source in sources.values():
        if source["owner_scope_digest"] != digest:
            raise LifecycleSecurityError("SCOPE_MISMATCH", "source scope differs")
        content = source["captured_content"]
        if content is not None and hashlib.sha256(content.encode()).hexdigest() != source["source_digest"]:
            raise LifecycleSecurityError("PROVENANCE_INVALID", "source digest differs")
    for provenance in rows.get("memory_provenance", []):
        source = sources.get(provenance["source_id"])
        chain = revisions.get(provenance["record_id"], [])
        if source is None or not any(
            row["revision"] == provenance["revision"] for row in chain
        ):
            raise LifecycleSecurityError("PROVENANCE_INVALID", "provenance target is absent")
        start, end = provenance["span_start"], provenance["span_end"]
        if (start is None) != (end is None) or (
            start is not None and (start < 0 or end < start)
        ):
            raise LifecycleSecurityError("PROVENANCE_INVALID", "source span is invalid")
        if provenance["quoted_digest"] is not None:
            content = source["captured_content"]
            if content is None:
                raise LifecycleSecurityError("PROVENANCE_INVALID", "quoted source is absent")
            encoded = content.encode()
            selected = encoded if start is None else encoded[start:end]
            try:
                selected.decode()
            except UnicodeDecodeError as error:
                raise LifecycleSecurityError(
                    "PROVENANCE_INVALID", "source span splits UTF-8"
                ) from error
            if hashlib.sha256(selected).hexdigest() != provenance["quoted_digest"]:
                raise LifecycleSecurityError("PROVENANCE_INVALID", "quoted digest differs")
    _validate_lineage(rows.get("memory_lineage", []), records, revisions)
    previous_cursor: tuple[int, int] | None = None
    for event in sorted(rows.get("memory_mutation_events", []), key=lambda row: (row["epoch"], row["sequence"])):
        cursor = (event["epoch"], event["sequence"])
        if event["owner_scope_digest"] != digest or (previous_cursor is not None and cursor <= previous_cursor):
            raise LifecycleSecurityError("CURSOR_INVALID", "mutation cursor is not monotonic")
        previous_cursor = cursor


def _revision_digest(revision: Mapping[str, Any]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"hypermid.memory.revision.v1\0")
    _hash_part(hasher, str(revision["record_id"]).encode())
    hasher.update(int(revision["revision"]).to_bytes(8, "big"))
    parent = revision["parent_revision_digest"]
    if parent is None:
        hasher.update(b"\x00")
    else:
        hasher.update(b"\x01")
        hasher.update(bytes.fromhex(parent))
    hasher.update(bytes.fromhex(revision["content_digest"]))
    _hash_part(hasher, revision["metadata_json"].encode())
    hasher.update(bytes.fromhex(revision["author_scope_digest"]))
    hasher.update(int(revision["authored_at_ms"]).to_bytes(8, "big"))
    return hasher.hexdigest()


def _hash_part(hasher: Any, value: bytes) -> None:
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _validate_lineage(
    lineage: list[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, list[Mapping[str, Any]]],
) -> None:
    acyclic = {"derived_from", "supersedes", "merged_from", "split_from"}
    edges: dict[str, set[str]] = {}
    for edge in lineage:
        child = edge["child_record_id"]
        parent = edge["parent_record_id"]
        if child not in records or parent not in records:
            raise LifecycleSecurityError("LINEAGE_INVALID", "lineage record is absent")
        if not any(
            row["revision"] == edge["child_revision"] for row in revisions.get(child, [])
        ) or not any(
            row["revision_digest"] == edge["parent_revision_digest"]
            for row in revisions.get(parent, [])
        ):
            raise LifecycleSecurityError("LINEAGE_INVALID", "lineage revision is absent")
        if edge["relation"] in acyclic:
            edges.setdefault(child, set()).add(parent)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record_id: str) -> None:
        if record_id in visiting:
            raise LifecycleSecurityError("LINEAGE_CYCLE", "lineage contains a cycle")
        if record_id in visited:
            return
        visiting.add(record_id)
        for parent in edges.get(record_id, set()):
            visit(parent)
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in edges:
        visit(record_id)


def _replace_scope(
    connection: sqlite3.Connection, bundle: Mapping[str, Any], scope: Scope
) -> None:
    _ensure_tombstone_table(connection)
    digest = _scope_digest(scope)
    entries = bundle["manifest"]["entries"]
    rows_by_table: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        payload = entry["payload"]
        rows_by_table.setdefault(payload["table"], []).append(payload["row"])
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        record_ids = [
            value
            for (value,) in connection.execute(
                "SELECT record_id FROM memory_records WHERE owner_scope_digest=?", (digest,)
            )
        ]
        source_ids = [
            value
            for (value,) in connection.execute(
                "SELECT source_id FROM memory_sources WHERE owner_scope_digest=?", (digest,)
            )
        ]
        _remove_scope_derivatives(connection, digest, record_ids)
        _delete_in(connection, "memory_verification_events", "record_id", record_ids)
        _delete_in(connection, "memory_provenance", "record_id", record_ids)
        _delete_in(connection, "memory_lineage", "child_record_id", record_ids)
        _delete_in(connection, "memory_lineage", "parent_record_id", record_ids)
        for table in ("episode_details", "smart_note_details", "summary_details", "memory_revisions"):
            _delete_in(connection, table, "record_id", record_ids)
        connection.execute("DELETE FROM memory_mutation_events WHERE owner_scope_digest=?", (digest,))
        if "memory_share_grants" in rows_by_table:
            connection.execute(
                "DELETE FROM memory_share_grants WHERE owner_scope_digest=?", (digest,)
            )
        connection.execute("DELETE FROM memory_records WHERE owner_scope_digest=?", (digest,))
        _delete_in(connection, "memory_sources", "source_id", source_ids)
        connection.execute("DELETE FROM memory_scopes WHERE scope_digest=?", (digest,))
        connection.execute("DELETE FROM hypermid_lifecycle_tombstones WHERE owner_scope_digest=?", (digest,))
        order = (
            "memory_scopes",
            "memory_share_grants",
            "memory_records",
            "memory_revisions",
            "episode_details",
            "smart_note_details",
            "summary_details",
            "memory_sources",
            "memory_provenance",
            "memory_lineage",
            "memory_verification_events",
            "memory_mutation_events",
            "hypermid_lifecycle_tombstones",
        )
        for table in order:
            for row in rows_by_table.get(table, []):
                columns = tuple(row)
                placeholders = ",".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
                    tuple(row[column] for column in columns),
                )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise LifecycleSecurityError("RESTORE_FOREIGN_KEY_INVALID", "restored scope breaks references")


def _validate_target_schema(
    connection: sqlite3.Connection,
    manifest: Mapping[str, Any],
    supported_schema: int,
) -> None:
    row = connection.execute(
        "SELECT current_version, compatibility_floor, schema_digest "
        "FROM hypermid_schema_version WHERE singleton=1"
    ).fetchone()
    expected = (
        int(manifest["schema_version"]),
        int(manifest["compatibility_floor"]),
        manifest["schema_digest"],
    )
    if row is None or int(row[0]) > supported_schema or int(row[1]) > supported_schema:
        raise LifecycleSecurityError("STORE_AHEAD", "active store schema is unsupported")
    if tuple(row) != expected:
        raise LifecycleSecurityError(
            "SCHEMA_MISMATCH", "backup and active store schema identities differ"
        )


def _validate_database_scope(
    connection: sqlite3.Connection, scope: Scope, bundle: Mapping[str, Any]
) -> None:
    restored = _export_bundle(
        connection,
        scope,
        include_grants=any(
            entry["category"] == "memory_share_grants"
            for entry in bundle["manifest"]["entries"]
        ),
    )
    if restored["manifest"]["stream_digest"] != bundle["manifest"]["stream_digest"]:
        raise LifecycleSecurityError("RESTORE_VALIDATION_FAILED", "restored rows differ from backup")


def _reject_purged_restore(active: Path, bundle: Mapping[str, Any], scope: Scope) -> None:
    connection = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
    try:
        exists = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='hypermid_lifecycle_tombstones')"
        ).fetchone() == (1,)
        if not exists:
            return
        purged = {
            value
            for (value,) in connection.execute(
                "SELECT record_id FROM hypermid_lifecycle_tombstones WHERE owner_scope_digest=? AND purged_at_ms IS NOT NULL",
                (_scope_digest(scope),),
            )
        }
        backed_up = {
            entry["payload"]["row"]["record_id"]
            for entry in bundle["manifest"]["entries"]
            if entry["category"] == "memory_records"
        }
        if purged & backed_up:
            raise LifecycleSecurityError(
                "STALE_BACKUP_TOMBSTONE", "backup would resurrect a physically purged record"
            )
    finally:
        connection.close()


def _export_queries(
    digest: str, include_grants: bool
) -> list[tuple[str, str, tuple[Any, ...]]]:
    queries = [
        ("memory_scopes", "SELECT * FROM memory_scopes WHERE scope_digest=? ORDER BY scope_digest", (digest,)),
        ("memory_records", "SELECT * FROM memory_records WHERE owner_scope_digest=? ORDER BY record_id", (digest,)),
        ("memory_revisions", "SELECT v.* FROM memory_revisions v JOIN memory_records r ON r.record_id=v.record_id WHERE r.owner_scope_digest=? ORDER BY v.record_id,v.revision", (digest,)),
        ("episode_details", "SELECT d.* FROM episode_details d JOIN memory_records r ON r.record_id=d.record_id WHERE r.owner_scope_digest=? ORDER BY d.record_id", (digest,)),
        ("smart_note_details", "SELECT d.* FROM smart_note_details d JOIN memory_records r ON r.record_id=d.record_id WHERE r.owner_scope_digest=? ORDER BY d.record_id", (digest,)),
        ("summary_details", "SELECT d.* FROM summary_details d JOIN memory_records r ON r.record_id=d.record_id WHERE r.owner_scope_digest=? ORDER BY d.record_id", (digest,)),
        ("memory_sources", "SELECT * FROM memory_sources WHERE owner_scope_digest=? ORDER BY source_id", (digest,)),
        ("memory_provenance", "SELECT p.* FROM memory_provenance p JOIN memory_records r ON r.record_id=p.record_id WHERE r.owner_scope_digest=? ORDER BY p.record_id,p.revision,p.source_id,p.span_start", (digest,)),
        ("memory_lineage", "SELECT l.* FROM memory_lineage l JOIN memory_records r ON r.record_id=l.child_record_id WHERE r.owner_scope_digest=? AND EXISTS(SELECT 1 FROM memory_records p WHERE p.record_id=l.parent_record_id AND p.owner_scope_digest=?) ORDER BY l.child_record_id,l.child_revision,l.parent_record_id,l.relation", (digest, digest)),
        ("memory_verification_events", "SELECT e.* FROM memory_verification_events e JOIN memory_records r ON r.record_id=e.record_id WHERE r.owner_scope_digest=? ORDER BY e.event_id", (digest,)),
        ("memory_mutation_events", "SELECT * FROM memory_mutation_events WHERE owner_scope_digest=? ORDER BY epoch,sequence", (digest,)),
        ("hypermid_lifecycle_tombstones", "SELECT * FROM hypermid_lifecycle_tombstones WHERE owner_scope_digest=? ORDER BY record_id", (digest,)),
    ]
    if include_grants:
        queries.insert(1, ("memory_share_grants", "SELECT * FROM memory_share_grants WHERE owner_scope_digest=? ORDER BY grant_id", (digest,)))
    return queries


def _query_dicts(
    connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...]
) -> list[dict[str, Any]]:
    cursor = connection.execute(query, parameters)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _ensure_tombstone_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hypermid_lifecycle_tombstones (
            record_id TEXT PRIMARY KEY,
            owner_scope_digest TEXT NOT NULL,
            revision_digest TEXT NOT NULL,
            deleted_at_ms INTEGER NOT NULL,
            retention_until_ms INTEGER NOT NULL,
            purged_at_ms INTEGER
        ) STRICT
        """
    )


def _remove_derivatives(connection: sqlite3.Connection, record_id: str) -> None:
    if _table_exists(connection, "memory_fts_rows"):
        rows = connection.execute(
            """SELECT f.rowid,r.owner_scope_digest,r.category,v.content
               FROM memory_fts_rows f
               JOIN memory_records r ON r.record_id=f.record_id
               JOIN memory_revisions v
                 ON v.record_id=r.record_id AND v.revision_digest=f.revision_digest
               WHERE f.record_id=?""",
            (record_id,),
        ).fetchall()
        for rowid, scope_digest, category, content in rows:
            connection.execute(
                "INSERT INTO memory_fts(memory_fts,rowid,record_id,owner_scope_digest,category,content) "
                "VALUES('delete',?,?,?,?,?)",
                (rowid, record_id, scope_digest, category, content),
            )
        connection.execute("DELETE FROM memory_fts_rows WHERE record_id=?", (record_id,))
    for table in ("memory_embeddings", "memory_retrieval_stats"):
        if _table_exists(connection, table):
            connection.execute(f"DELETE FROM {table} WHERE record_id=?", (record_id,))


def _remove_scope_derivatives(
    connection: sqlite3.Connection, scope_digest: str, record_ids: list[str]
) -> None:
    for record_id in record_ids:
        _remove_derivatives(connection, record_id)
    if _table_exists(connection, "source_index_documents"):
        documents = connection.execute(
            "SELECT d.document_id,f.rowid,d.source_kind,d.content "
            "FROM source_index_documents d LEFT JOIN source_fts_rows f "
            "ON f.document_id=d.document_id WHERE d.owner_scope_digest=?",
            (scope_digest,),
        ).fetchall()
        for document_id, rowid, source_kind, content in documents:
            if rowid is not None:
                connection.execute(
                    "INSERT INTO source_fts(source_fts,rowid,document_id,owner_scope_digest,source_kind,content) "
                    "VALUES('delete',?,?,?,?,?)",
                    (rowid, document_id, scope_digest, source_kind, content),
                )
        connection.execute(
            "DELETE FROM source_fts_rows WHERE document_id IN "
            "(SELECT document_id FROM source_index_documents WHERE owner_scope_digest=?)",
            (scope_digest,),
        )
        connection.execute(
            "DELETE FROM source_index_documents WHERE owner_scope_digest=?", (scope_digest,)
        )
    for table in (
        "source_index_state",
        "file_predicate_decisions",
        "maintenance_leases",
        "export_manifests",
    ):
        if _table_exists(connection, table):
            connection.execute(
                f"DELETE FROM {table} WHERE owner_scope_digest=?", (scope_digest,)
            )
    if _table_exists(connection, "import_batches"):
        batch_ids = [
            row[0]
            for row in connection.execute(
                "SELECT batch_id FROM import_batches WHERE target_scope_digest=?",
                (scope_digest,),
            )
        ]
        _delete_in(connection, "import_items", "batch_id", batch_ids)
        connection.execute(
            "DELETE FROM import_batches WHERE target_scope_digest=?", (scope_digest,)
        )
    if _table_exists(connection, "maintenance_jobs"):
        job_ids = [
            row[0]
            for row in connection.execute(
                "SELECT job_id FROM maintenance_jobs WHERE owner_scope_digest=?",
                (scope_digest,),
            )
        ]
        _delete_in(connection, "embedding_jobs", "job_id", job_ids)
        connection.execute(
            "DELETE FROM maintenance_jobs WHERE owner_scope_digest=?", (scope_digest,)
        )
    if _table_exists(connection, "embedding_registrations"):
        connection.execute(
            "DELETE FROM embedding_registrations WHERE owner_scope_digest=?", (scope_digest,)
        )
    if _table_exists(connection, "recovery_snapshots"):
        connection.execute("DELETE FROM recovery_snapshots")


def _delete_in(
    connection: sqlite3.Connection, table: str, column: str, values: list[str]
) -> None:
    if not values or not _table_exists(connection, table):
        return
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?)",
        (table,),
    ).fetchone() == (1,)


def _scope_digest(scope: Scope) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"hypermid.memory.scope.v1\0")
    for value in (str(scope.owner_id), str(scope.project_id)):
        encoded = value.encode()
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    if scope.workspace_id is None:
        hasher.update(b"\x00")
    else:
        hasher.update(b"\x01")
        encoded = str(scope.workspace_id).encode()
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reject_forbidden(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_FIELDS:
                raise LifecycleSecurityError(
                    "FORBIDDEN_BACKUP_FIELD", "backup contains secret or transient state"
                )
            _reject_forbidden(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_forbidden(child)


def _require_no_unknown_effects(states: Iterable[EffectState | str]) -> None:
    if any(EffectState(state) is EffectState.UNKNOWN for state in states):
        raise LifecycleSecurityError(
            "UNKNOWN_EFFECT_BLOCKS_RESTORE",
            "restore cannot replace authority while an effect outcome is unknown",
        )


def _require_regular(path: Path, code: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise LifecycleSecurityError(code, "required lifecycle file is absent") from error
    if not stat.S_ISREG(mode):
        raise LifecycleSecurityError(code, "lifecycle path is not a regular file")


def _acquire_activation_lease(active_path: Path) -> int:
    lease_path = Path(f"{active_path}.lease")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lease_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise LifecycleSecurityError(
                "ACTIVE_STORE_BUSY", "active store lease is not a regular file"
            )
        os.fchmod(descriptor, 0o600)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise LifecycleSecurityError(
            "ACTIVE_STORE_BUSY", "active store lease is held by a live writer"
        ) from error
    except LifecycleSecurityError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise LifecycleSecurityError(
            "ACTIVE_STORE_BUSY", "active store lease could not be acquired"
        ) from error


def _release_activation_lease(descriptor: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _checkpoint_inactive_store(active_path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{active_path}?mode=rw",
            uri=True,
            timeout=0,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout=0")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise LifecycleSecurityError(
                "ACTIVE_STORE_BUSY", "active SQLite writer or reader blocks checkpoint"
            )
    except LifecycleSecurityError:
        raise
    except sqlite3.Error as error:
        raise LifecycleSecurityError(
            "ACTIVE_STORE_BUSY", "active SQLite store could not be checkpointed"
        ) from error
    finally:
        if connection is not None:
            connection.close()

    try:
        wal = Path(f"{active_path}-wal")
        if wal.exists() and wal.stat().st_size != 0:
            raise LifecycleSecurityError(
                "ACTIVE_STORE_BUSY", "active SQLite WAL retained uncheckpointed frames"
            )
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{active_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
    except LifecycleSecurityError:
        raise
    except OSError as error:
        raise LifecycleSecurityError(
            "ACTIVE_STORE_BUSY", "checkpointed SQLite sidecars could not be retired"
        ) from error


def _effective_sqlite_digest(active_path: Path) -> Digest:
    descriptor, snapshot_name = tempfile.mkstemp(
        prefix=".hypermid-active-evidence-", dir=active_path.parent
    )
    os.close(descriptor)
    snapshot_path = Path(snapshot_name)
    try:
        os.chmod(snapshot_path, 0o600)
        source = sqlite3.connect(f"file:{active_path}?mode=ro", uri=True)
        snapshot = sqlite3.connect(snapshot_path)
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
            source.close()
        return _file_digest(snapshot_path)
    except sqlite3.Error as error:
        raise LifecycleSecurityError(
            "ACTIVE_STORE_BUSY", "active SQLite snapshot could not be reviewed"
        ) from error
    finally:
        snapshot_path.unlink(missing_ok=True)


def _sqlite_authority_fingerprint(active_path: Path) -> Digest:
    hasher = hashlib.sha256()
    hasher.update(b"hypermid.sqlite.authority.v1\0")
    for path in (active_path, Path(f"{active_path}-wal")):
        if not path.exists():
            hasher.update(b"\x00")
            continue
        _require_regular(path, "ACTIVE_STORE_INVALID")
        hasher.update(b"\x01")
        size = path.stat().st_size
        hasher.update(size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                hasher.update(chunk)
    return Digest(hasher.hexdigest())


def _file_digest(path: Path) -> Digest:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            hasher.update(chunk)
    return Digest(hasher.hexdigest())


def _atomic_private_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".hypermid-backup-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _sync_directory(path.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
