from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .credential_authority import CredentialStoreSecretVault

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol

from .client import HypermidOutcomeUnknown, HypermidRemoteError
from .contracts import MemoryOperation, MutationRequest, RevisionPrecondition
from .effects import EffectLifecycleState, EffectService
from .foundation import Cursor, Digest, EffectState, Id, Scope, Trace
from .lifecycle_security import (
    BackupKey,
    EncryptedMemoryBundleReceipt,
    LifecycleSecurity,
    LifecycleSecurityError,
    decrypt_memory_bundle,
    encrypt_memory_bundle,
    purge_record,
    tombstone_record,
)
from .memory_client import MemoryClient
from .network_policy import SecretHandle, SecretVault
from .portability import MemoryExportBundle, MemoryImportBatch
from .sources import scope_digest

_ACTIONS = frozenset({"backup", "restore", "tombstone", "purge"})
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PLAN_ID = re.compile(r"^security-(?:backup|restore|tombstone|purge)-[a-f0-9]{24}$")
_JOB_ID = re.compile(r"^job-[a-f0-9]{24}$")
_PLAN_TTL = timedelta(minutes=10)


class SecurityOperationsError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message[:512])


@dataclass(slots=True)
class _Plan:
    action: str
    plan_id: str
    plan_digest: Digest
    authority_digest: Digest
    created_at: datetime
    expires_at: datetime
    params: dict[str, Any]
    secret_handle: SecretHandle | None = None
    memory_bundle: MemoryExportBundle | None = None
    memory_batch: MemoryImportBatch | None = None
    memory_request: MutationRequest | None = None
    encrypted_receipt: EncryptedMemoryBundleReceipt | None = None
    staging_id: str | None = None
    source_digest: Digest | None = None
    cursor: Cursor | None = None


class _WindowsFileLock(Protocol):
    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, descriptor: int, mode: int, count: int) -> None: ...


class SecurityOperations:
    """Owner-bound lifecycle authority used by Hypermid operator handlers."""

    def __init__(
        self,
        *,
        scope: Scope,
        principal_id: str,
        active_path: str | os.PathLike[str],
        secret_vault: SecretVault | CredentialStoreSecretVault,
        supported_schema: int,
        memory_client: MemoryClient | None = None,
        credential_handles: Mapping[str, SecretHandle] | None = None,
    ) -> None:
        if not principal_id:
            raise ValueError("principal_id is required")
        if isinstance(supported_schema, bool) or supported_schema < 1:
            raise ValueError("supported_schema must be positive")
        self.scope = scope
        self.principal_id = principal_id
        self.active_path = Path(active_path)
        self.secret_vault = secret_vault
        self.supported_schema = supported_schema
        self.memory_client = memory_client
        self.credential_handles = dict(credential_handles or {})
        self.lifecycle = LifecycleSecurity()
        self.journal_root = self.active_path.parent / "security-operations"
        _private_directory(self.journal_root)
        _private_directory(self.journal_root / "plans")
        _private_directory(self.journal_root / "receipts")
        _private_directory(self.journal_root / "locks")
        self._plans: dict[str, _Plan] = {}
        self._receipts: dict[str, dict[str, Any]] = {}

    async def plan(
        self, action: str, payload: Mapping[str, Any], scope: Scope
    ) -> Mapping[str, Any]:
        self._require_scope(scope)
        if action not in _ACTIONS:
            raise SecurityOperationsError(
                "UNSUPPORTED_OPERATION", "security lifecycle action is unsupported"
            )
        if not self.active_path.is_file() or self.active_path.is_symlink():
            raise SecurityOperationsError(
                "ACTIVE_STORE_INVALID", "active SQLite store is unavailable"
            )
        params = self._plan_params(action, payload)
        now = datetime.now(UTC)
        authority_digest: Digest
        staging_id: str | None = None
        source_digest: Digest | None = None
        cursor: Cursor | None = None
        secret_handle: SecretHandle | None = None
        memory_bundle: MemoryExportBundle | None = None
        memory_batch: MemoryImportBatch | None = None
        memory_request: MutationRequest | None = None
        encrypted_receipt: EncryptedMemoryBundleReceipt | None = None
        if action == "restore":
            memory = self._memory()
            params.update(await self._effect_evidence(memory))
            if params["unknown_effect_count"]:
                raise LifecycleSecurityError(
                    "UNKNOWN_EFFECT_BLOCKS_RESTORE",
                    "restore cannot replace authority while an effect outcome is unknown",
                )
            if params["unresolved_effect_count"]:
                raise LifecycleSecurityError(
                    "UNRESOLVED_EFFECTS_BLOCK_RESTORE",
                    "restore cannot replace authority while effects remain unresolved",
                )
            source_digest = Digest(params["source_digest"])
            handle = SecretHandle(params.pop("credential_handle"))
            secret_handle = handle
            bundle_and_receipt = self.secret_vault.dispatch_with_secret(
                handle,
                principal_id=self.principal_id,
                operation="hypermid.restore",
                dispatch=lambda material: decrypt_memory_bundle(
                    params["artifact_path"],
                    self.scope,
                    BackupKey(material),
                    expected_artifact_digest=source_digest,
                ),
            )
            memory_bundle, encrypted_receipt = bundle_and_receipt
            cursor = memory_bundle.manifest.cursor
            batch_id = Id(f"import-{secrets.token_hex(12)}")
            memory_request = _memory_request(
                self.scope, MemoryOperation.IMPORT, "restore"
            )
            memory_batch = await memory.stage_import(
                memory_request,
                batch_id=batch_id,
                bundle=memory_bundle,
                target_scope=self.scope,
                scope_mapping={},
            )
            if memory_batch.state != "validated" or memory_batch.rejected:
                raise SecurityOperationsError(
                    "IMPORT_REJECTED", "canonical memory import did not validate"
                )
            staging_id = str(memory_batch.batch_id)
            params["credential_binding_digest"] = _binding_digest(handle)
            params.update(_bundle_identity(memory_bundle))
            params["batch_source_digest"] = str(memory_batch.manifest.source_digest)
            authority_digest = _database_digest(self.active_path)
        elif action == "backup":
            memory = self._memory()
            handle = SecretHandle(params.pop("credential_handle"))
            secret_handle = handle
            self.secret_vault.dispatch_with_secret(
                handle,
                principal_id=self.principal_id,
                operation="hypermid.backup",
                dispatch=lambda material: BackupKey(material),
            )
            export_id = _id(params.pop("export_id"), "export_id")
            memory_request = _memory_request(
                self.scope, MemoryOperation.EXPORT, "backup"
            )
            memory_bundle = await memory.export_scope(
                memory_request,
                export_id=Id(f"review-{secrets.token_hex(12)}"),
                include_grants=False,
            )
            if memory_bundle.manifest.scope != self.scope:
                raise SecurityOperationsError(
                    "SCOPE_MISMATCH", "memory export scope differs from authority"
                )
            cursor = memory_bundle.manifest.cursor
            params["credential_binding_digest"] = _binding_digest(handle)
            params["export_id"] = str(export_id)
            params.update(_backup_content_identity(memory_bundle))
            authority_digest = _backup_authority_digest(memory_bundle)
        else:
            authority_digest = _database_digest(self.active_path)
            self._validate_record_plan(action, params)
            cursor = _scope_cursor(self.active_path, self.scope)
        plan_id = f"security-{action}-{secrets.token_hex(12)}"
        expires = now + _PLAN_TTL
        unsigned = self._plan_wire(
            action,
            plan_id,
            authority_digest,
            now,
            expires,
            params,
            staging_id,
            source_digest,
            cursor,
        )
        plan_digest = Digest.sha256(_canonical(unsigned))
        plan = _Plan(
            action=action,
            plan_id=plan_id,
            plan_digest=plan_digest,
            authority_digest=authority_digest,
            created_at=now,
            expires_at=expires,
            params=params,
            secret_handle=secret_handle,
            memory_bundle=memory_bundle,
            memory_batch=memory_batch,
            memory_request=memory_request,
            encrypted_receipt=encrypted_receipt,
            staging_id=staging_id,
            source_digest=source_digest,
            cursor=cursor,
        )
        self._plans[plan_id] = plan
        self._persist_plan(plan)
        return unsigned | {"plan_digest": str(plan_digest)}

    async def apply(
        self, action: str, payload: Mapping[str, Any], scope: Scope
    ) -> Mapping[str, Any]:
        self._require_scope(scope)
        plan_id = _text(payload.get("plan_id"), "plan_id", 160)
        if _PLAN_ID.fullmatch(plan_id) is None:
            raise SecurityOperationsError(
                "PLAN_NOT_FOUND", "reviewed plan is unavailable"
            )
        with _exclusive_journal_lock(self._plan_lock_path(plan_id)):
            return await self._apply_locked(action, payload, plan_id)

    async def _apply_locked(
        self, action: str, payload: Mapping[str, Any], plan_id: str
    ) -> Mapping[str, Any]:
        reviewed = _digest(payload.get("plan_digest"), "plan_digest")
        plan = self._plans.get(plan_id) or self._load_plan(plan_id)
        if plan is None or plan.action != action:
            raise SecurityOperationsError(
                "PLAN_NOT_FOUND", "reviewed plan is unavailable"
            )
        if plan.plan_digest != reviewed:
            raise SecurityOperationsError(
                "PLAN_DIGEST_MISMATCH", "reviewed plan digest does not match"
            )
        if datetime.now(UTC) >= plan.expires_at:
            self._discard_plan(plan)
            raise SecurityOperationsError("PLAN_EXPIRED", "reviewed plan expired")
        if (
            action in {"restore", "tombstone", "purge"}
            and payload.get("confirm_destructive") is not True
        ):
            raise SecurityOperationsError(
                "CONFIRMATION_REQUIRED", "destructive operation requires confirmation"
            )
        if action == "purge" and payload.get("confirm_purge") is not True:
            raise SecurityOperationsError(
                "PURGE_CONFIRMATION_REQUIRED",
                "physical purge requires distinct confirmation",
            )
        if (
            action != "backup"
            and _database_digest(self.active_path) != plan.authority_digest
        ):
            self._discard_plan(plan)
            raise SecurityOperationsError(
                "AUTHORITY_CHANGED", "active store changed after plan review"
            )
        started = datetime.now(UTC)
        job_id = f"job-{secrets.token_hex(12)}"
        try:
            await self._rehydrate_plan(plan)
            evidence = await self._execute(plan)
            receipt = self._receipt(
                plan,
                job_id,
                started,
                state="committed",
                effect_state=EffectState.COMMITTED,
                evidence=evidence,
            )
        except HypermidOutcomeUnknown as error:
            receipt = self._receipt(
                plan,
                job_id,
                started,
                state="outcome_unknown",
                effect_state=EffectState.UNKNOWN,
                evidence=_source_evidence(plan),
                error={"code": error.error.code, "message": str(error)},
            )
        except HypermidRemoteError as error:
            if error.error.effect_state is not EffectState.UNKNOWN:
                raise
            receipt = self._receipt(
                plan,
                job_id,
                started,
                state="outcome_unknown",
                effect_state=EffectState.UNKNOWN,
                evidence=_source_evidence(plan),
                error={"code": error.error.code, "message": str(error)},
            )
        except LifecycleSecurityError as error:
            if error.code == "ACTIVATION_OUTCOME_UNKNOWN":
                receipt = self._receipt(
                    plan,
                    job_id,
                    started,
                    state="outcome_unknown",
                    effect_state=EffectState.UNKNOWN,
                    evidence=_source_evidence(plan),
                    error={"code": error.code, "message": str(error)},
                )
            else:
                self._discard_plan(plan)
                raise
        self._plans.pop(plan.plan_id, None)
        self._remove_plan(plan.plan_id)
        self._receipts[job_id] = receipt
        self._persist_receipt(receipt)
        return receipt

    async def status(self, job_id: str, scope: Scope) -> Mapping[str, Any]:
        self._require_scope(scope)
        receipt = self._receipts.get(job_id) or self._load_receipt(job_id)
        if receipt is None:
            raise SecurityOperationsError(
                "JOB_NOT_FOUND", "security lifecycle job is absent"
            )
        return receipt

    async def recover(self, job_id: str, scope: Scope) -> Mapping[str, Any]:
        return await self.status(job_id, scope)

    async def _execute(self, plan: _Plan) -> dict[str, Any]:
        if plan.action == "backup":
            assert plan.secret_handle is not None and plan.memory_bundle is not None
            backup = self.secret_vault.dispatch_with_secret(
                plan.secret_handle,
                principal_id=self.principal_id,
                operation="hypermid.backup",
                dispatch=lambda material: encrypt_memory_bundle(
                    cast(MemoryExportBundle, plan.memory_bundle),
                    plan.params["destination"],
                    BackupKey(material),
                ),
            )
            return {
                "artifact_digest": str(backup.artifact_digest),
                "artifact_bytes": backup.bytes,
                "artifact_path": plan.params["destination"],
                "credential_ref": plan.params["credential_ref"],
                "source_digest": str(backup.source_digest),
                "export_id": backup.export_id,
                "stream_digest": str(backup.stream_digest),
                "item_count": backup.item_count,
                "record_count": backup.record_count,
                "cursor": backup.cursor.to_wire(),
            }
        if plan.action == "restore":
            assert (
                plan.secret_handle is not None
                and plan.memory_bundle is not None
                and plan.memory_batch is not None
                and plan.memory_request is not None
                and plan.encrypted_receipt is not None
            )
            self.secret_vault.dispatch_with_secret(
                plan.secret_handle,
                principal_id=self.principal_id,
                operation="hypermid.restore",
                dispatch=lambda material: BackupKey(material),
            )
            if plan.params["unknown_effect_count"]:
                raise LifecycleSecurityError(
                    "UNKNOWN_EFFECT_BLOCKS_RESTORE",
                    "restore cannot replace authority while an effect outcome is unknown",
                )
            applied = await self._memory().apply_import(
                plan.memory_request,
                batch=plan.memory_batch,
                bundle=plan.memory_bundle,
            )
            if applied.state != "applied" or applied.rejected:
                raise SecurityOperationsError(
                    "IMPORT_NOT_APPLIED", "canonical memory import did not apply"
                )
            restored = plan.encrypted_receipt
            return {
                "artifact_digest": str(restored.artifact_digest),
                "artifact_bytes": restored.bytes,
                "credential_ref": plan.params["credential_ref"],
                "source_digest": str(restored.source_digest),
                "export_id": restored.export_id,
                "stream_digest": str(restored.stream_digest),
                "item_count": restored.item_count,
                "record_count": restored.record_count,
                "batch_id": str(applied.batch_id),
                "batch_state": applied.state,
                "batch_source_digest": str(applied.manifest.source_digest),
                "replayed": applied.replayed,
                "cursor": restored.cursor.to_wire(),
            }
        connection = sqlite3.connect(self.active_path)
        try:
            if plan.action == "tombstone":
                tombstone_record(
                    connection,
                    self.scope,
                    self.scope,
                    plan.params["record_id"],
                    Digest(plan.params["revision_digest"]),
                    now_ms=plan.params["now_ms"],
                    retention_until_ms=plan.params["retention_until_ms"],
                )
            else:
                purge_record(
                    connection,
                    self.scope,
                    self.scope,
                    plan.params["record_id"],
                    now_ms=plan.params["now_ms"],
                )
            cursor = _scope_cursor_connection(connection, self.scope)
            return {"cursor": cursor.to_wire()}
        finally:
            connection.close()

    async def _rehydrate_plan(self, plan: _Plan) -> None:
        if plan.action not in {"backup", "restore"}:
            return
        memory = self._memory()
        handle = plan.secret_handle or self.credential_handles.get(plan.action)
        if handle is None or _binding_digest(handle) != plan.params.get(
            "credential_binding_digest"
        ):
            raise SecurityOperationsError(
                "SECURITY_CREDENTIAL_UNAVAILABLE",
                "reviewed credential binding is unavailable",
            )
        if plan.action == "backup":
            self.secret_vault.dispatch_with_secret(
                handle,
                principal_id=self.principal_id,
                operation="hypermid.backup",
                dispatch=lambda material: BackupKey(material),
            )
            request = _memory_request(
                self.scope, MemoryOperation.EXPORT, "backup-apply"
            )
            bundle = await memory.export_scope(
                request,
                export_id=Id(plan.params["export_id"]),
                include_grants=False,
            )
            self._verify_rehydrated_backup(plan, bundle)
            if _backup_authority_digest(bundle) != plan.authority_digest:
                raise SecurityOperationsError(
                    "AUTHORITY_CHANGED", "backup source changed after plan review"
                )
            plan.secret_handle = handle
            plan.memory_request = request
            plan.memory_bundle = bundle
            return

        if plan.source_digest is None or plan.staging_id is None:
            raise SecurityOperationsError(
                "PLAN_CORRUPT", "restore plan is missing source evidence"
            )
        effect_evidence = await self._effect_evidence(memory)
        if effect_evidence["unknown_effect_count"]:
            raise LifecycleSecurityError(
                "UNKNOWN_EFFECT_BLOCKS_RESTORE",
                "restore cannot replace authority while an effect outcome is unknown",
            )
        if effect_evidence["unresolved_effect_count"]:
            raise LifecycleSecurityError(
                "UNRESOLVED_EFFECTS_BLOCK_RESTORE",
                "restore cannot replace authority while effects remain unresolved",
            )
        if any(
            plan.params.get(field) != effect_evidence[field]
            for field in (
                "unresolved_effect_count",
                "unknown_effect_count",
                "effect_snapshot_digest",
            )
        ):
            raise SecurityOperationsError(
                "EFFECT_AUTHORITY_CHANGED",
                "unresolved effect authority changed after plan review",
            )
        bundle, encrypted = self.secret_vault.dispatch_with_secret(
            handle,
            principal_id=self.principal_id,
            operation="hypermid.restore",
            dispatch=lambda material: decrypt_memory_bundle(
                plan.params["artifact_path"],
                self.scope,
                BackupKey(material),
                expected_artifact_digest=cast(Digest, plan.source_digest),
            ),
        )
        self._verify_rehydrated_bundle(plan, bundle)
        request = _memory_request(self.scope, MemoryOperation.IMPORT, "restore-apply")
        batch = await memory.stage_import(
            request,
            batch_id=Id(plan.staging_id),
            bundle=bundle,
            target_scope=self.scope,
            scope_mapping={},
        )
        if (
            batch.state != "validated"
            or batch.rejected
            or str(batch.manifest.source_digest)
            != plan.params.get("batch_source_digest")
        ):
            raise SecurityOperationsError(
                "IMPORT_REJECTED", "canonical memory import no longer validates"
            )
        plan.secret_handle = handle
        plan.memory_bundle = bundle
        plan.memory_batch = batch
        plan.memory_request = request
        plan.encrypted_receipt = encrypted

    async def _effect_evidence(self, memory: MemoryClient) -> dict[str, Any]:
        unresolved = await EffectService(memory.client).list_unresolved(self.scope)
        evidence = [
            {
                "effect_id": str(effect.effect_id),
                "state": effect.state.value,
                "input_digest": str(effect.input_digest),
            }
            for effect in sorted(
                unresolved.effects, key=lambda item: str(item.effect_id)
            )
        ]
        return {
            "unresolved_effect_count": len(evidence),
            "unknown_effect_count": sum(
                effect.state is EffectLifecycleState.UNKNOWN
                for effect in unresolved.effects
            ),
            "effect_snapshot_digest": str(Digest.sha256(_canonical(evidence))),
        }

    def _verify_rehydrated_bundle(
        self, plan: _Plan, bundle: MemoryExportBundle
    ) -> None:
        identity = _bundle_identity(bundle)
        for field, value in identity.items():
            if plan.params.get(field) != value:
                raise SecurityOperationsError(
                    "AUTHORITY_CHANGED",
                    "canonical bundle changed after plan review",
                )

    def _verify_rehydrated_backup(
        self, plan: _Plan, bundle: MemoryExportBundle
    ) -> None:
        identity = _backup_content_identity(bundle)
        for field, value in identity.items():
            if plan.params.get(field) != value:
                raise SecurityOperationsError(
                    "AUTHORITY_CHANGED",
                    "backup source changed after plan review",
                )

    def _persist_plan(self, plan: _Plan) -> None:
        value: dict[str, Any] = {
            "version": 1,
            "scope": self.scope.to_wire(),
            "action": plan.action,
            "plan_id": plan.plan_id,
            "plan_digest": str(plan.plan_digest),
            "authority_digest": str(plan.authority_digest),
            "created_at": _timestamp(plan.created_at),
            "expires_at": _timestamp(plan.expires_at),
            "params": plan.params,
            "staging_id": plan.staging_id,
            "source_digest": (
                str(plan.source_digest) if plan.source_digest is not None else None
            ),
            "cursor": plan.cursor.to_wire() if plan.cursor is not None else None,
        }
        _atomic_private_json(self._plan_path(plan.plan_id), value)

    def _load_plan(self, plan_id: str) -> _Plan | None:
        if _PLAN_ID.fullmatch(plan_id) is None:
            return None
        value = _read_private_json(self._plan_path(plan_id))
        if value is None:
            return None
        expected = {
            "version",
            "scope",
            "action",
            "plan_id",
            "plan_digest",
            "authority_digest",
            "created_at",
            "expires_at",
            "params",
            "staging_id",
            "source_digest",
            "cursor",
        }
        if set(value) != expected or value.get("version") != 1:
            raise SecurityOperationsError(
                "PLAN_CORRUPT", "security plan journal is invalid"
            )
        if (
            value.get("scope") != self.scope.to_wire()
            or value.get("plan_id") != plan_id
        ):
            raise SecurityOperationsError(
                "AUTHORIZATION_DENIED", "security plan belongs to another authority"
            )
        action = value.get("action")
        params = value.get("params")
        if action not in _ACTIONS or not isinstance(params, dict):
            raise SecurityOperationsError(
                "PLAN_CORRUPT", "security plan journal is invalid"
            )
        try:
            cursor_value = value.get("cursor")
            plan = _Plan(
                action=action,
                plan_id=plan_id,
                plan_digest=Digest(value["plan_digest"]),
                authority_digest=Digest(value["authority_digest"]),
                created_at=_parse_timestamp(value["created_at"]),
                expires_at=_parse_timestamp(value["expires_at"]),
                params=dict(params),
                staging_id=value.get("staging_id"),
                source_digest=(
                    Digest(value["source_digest"])
                    if value.get("source_digest") is not None
                    else None
                ),
                cursor=(
                    Cursor.from_wire(cursor_value)
                    if isinstance(cursor_value, Mapping)
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SecurityOperationsError(
                "PLAN_CORRUPT", "security plan journal is invalid"
            ) from error
        unsigned = self._plan_wire(
            plan.action,
            plan.plan_id,
            plan.authority_digest,
            plan.created_at,
            plan.expires_at,
            plan.params,
            plan.staging_id,
            plan.source_digest,
            plan.cursor,
        )
        if Digest.sha256(_canonical(unsigned)) != plan.plan_digest:
            raise SecurityOperationsError(
                "PLAN_DIGEST_MISMATCH", "security plan journal changed after review"
            )
        self._plans[plan_id] = plan
        return plan

    def _persist_receipt(self, receipt: Mapping[str, Any]) -> None:
        job_id = receipt.get("job_id")
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise SecurityOperationsError(
                "RECEIPT_INVALID", "security receipt is invalid"
            )
        body = dict(receipt)
        _atomic_private_json(
            self._receipt_path(job_id),
            {
                "version": 1,
                "receipt": body,
                "digest": str(Digest.sha256(_canonical(body))),
            },
        )

    def _load_receipt(self, job_id: str) -> dict[str, Any] | None:
        if _JOB_ID.fullmatch(job_id) is None:
            return None
        value = _read_private_json(self._receipt_path(job_id))
        if value is None:
            return None
        if set(value) != {"version", "receipt", "digest"} or value.get("version") != 1:
            raise SecurityOperationsError(
                "RECEIPT_CORRUPT", "security receipt journal is invalid"
            )
        receipt = value.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("job_id") != job_id:
            raise SecurityOperationsError(
                "RECEIPT_CORRUPT", "security receipt journal is invalid"
            )
        if receipt.get("scope") != self.scope.to_wire() or value.get("digest") != str(
            Digest.sha256(_canonical(receipt))
        ):
            raise SecurityOperationsError(
                "RECEIPT_CORRUPT", "security receipt journal failed validation"
            )
        self._receipts[job_id] = receipt
        return receipt

    def _plan_path(self, plan_id: str) -> Path:
        return self.journal_root / "plans" / f"{plan_id}.json"

    def _receipt_path(self, job_id: str) -> Path:
        return self.journal_root / "receipts" / f"{job_id}.json"

    def _plan_lock_path(self, plan_id: str) -> Path:
        return self.journal_root / "locks" / f"{plan_id}.lock"

    def _remove_plan(self, plan_id: str) -> None:
        path = self._plan_path(plan_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _validate_record_plan(self, action: str, params: Mapping[str, Any]) -> None:
        connection = sqlite3.connect(f"file:{self.active_path}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT status,current_revision_digest,retention_until_ms "
                "FROM memory_records WHERE record_id=? AND owner_scope_digest=?",
                (params["record_id"], str(scope_digest(self.scope))),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise SecurityOperationsError("RECORD_NOT_FOUND", "owned record is absent")
        if action == "tombstone":
            if row[0] == "tombstoned" or row[1] != params["revision_digest"]:
                raise SecurityOperationsError(
                    "STALE_RECORD", "record revision or lifecycle state changed"
                )
            if params["retention_until_ms"] <= params["now_ms"]:
                raise SecurityOperationsError(
                    "INVALID_RETENTION", "recovery window must be positive"
                )
        elif row[0] != "tombstoned" or row[2] is None or params["now_ms"] < row[2]:
            raise SecurityOperationsError(
                "RETENTION_NOT_ELAPSED", "record is not eligible for physical purge"
            )

    def _plan_params(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if action == "backup":
            destination = _absolute_path(payload.get("destination"), "destination")
            if destination == self.active_path.resolve():
                raise SecurityOperationsError(
                    "INVALID_DESTINATION", "backup cannot replace the active store"
                )
            if payload.get("include_grants") is True:
                raise SecurityOperationsError(
                    "AUTHORIZATION_EXPORT_DENIED", "reusable grants cannot enter backup"
                )
            export_value = payload.get("export_id")
            export_id = (
                _id(export_value, "export_id")
                if export_value is not None
                else Id(f"export-{secrets.token_hex(12)}")
            )
            return {
                "destination": os.fspath(destination),
                "credential_handle": _text(
                    payload.get("credential_handle"), "credential_handle", 256
                ),
                "credential_ref": _text(
                    payload.get("credential_ref", "provided-handle"),
                    "credential_ref",
                    160,
                ),
                "export_id": str(export_id),
            }
        if action == "restore":
            artifact = _absolute_path(payload.get("artifact_path"), "artifact_path")
            if not artifact.is_file() or artifact.is_symlink():
                raise SecurityOperationsError(
                    "BACKUP_INVALID", "backup artifact is unavailable"
                )
            if "effect_states" in payload:
                raise SecurityOperationsError(
                    "INVALID_REQUEST", "effect states are selected by the server"
                )
            return {
                "artifact_path": os.fspath(artifact),
                "source_digest": str(
                    _digest(payload.get("source_digest"), "source_digest")
                ),
                "credential_handle": _text(
                    payload.get("credential_handle"), "credential_handle", 256
                ),
                "credential_ref": _text(
                    payload.get("credential_ref", "provided-handle"),
                    "credential_ref",
                    160,
                ),
            }
        record_id = _text(payload.get("record_id"), "record_id", 160)
        now_ms = _uint(payload.get("now_ms"), "now_ms")
        if action == "tombstone":
            retention = _uint(payload.get("retention_until_ms"), "retention_until_ms")
            return {
                "record_id": record_id,
                "revision_digest": str(
                    _digest(payload.get("revision_digest"), "revision_digest")
                ),
                "now_ms": now_ms,
                "retention_until_ms": retention,
            }
        return {"record_id": record_id, "now_ms": now_ms}

    def _plan_wire(
        self,
        action: str,
        plan_id: str,
        authority_digest: Digest,
        created: datetime,
        expires: datetime,
        params: Mapping[str, Any],
        staging_id: str | None,
        source_digest: Digest | None,
        cursor: Cursor | None,
    ) -> dict[str, Any]:
        destructive = action in {"restore", "tombstone", "purge"}
        effect = (
            "delete_authoritative"
            if action == "purge"
            else ("write_authoritative" if destructive else "read")
        )
        result: dict[str, Any] = {
            "plan_id": plan_id,
            "operation": f"security.{action}.apply",
            "scope": self.scope.to_wire(),
            "created_at": _timestamp(created),
            "expires_at": _timestamp(expires),
            "authority_digest": str(authority_digest),
            "destructive": destructive,
            "restart_required": action == "restore",
            "steps": [
                {
                    "id": "verify-and-apply",
                    "title": f"Verify and apply {action}",
                    "effect": effect,
                    "state": "planned",
                }
            ],
            "blockers": [],
            "params_digest": str(Digest.sha256(_canonical(params))),
        }
        if staging_id is not None:
            result["staging_id"] = staging_id
        if source_digest is not None:
            result["source_digest"] = str(source_digest)
        if cursor is not None:
            result["cursor"] = cursor.to_wire()
        for field in (
            "export_id",
            "stream_digest",
            "bundle_digest",
            "item_count",
            "record_count",
            "batch_source_digest",
            "credential_ref",
            "unresolved_effect_count",
            "unknown_effect_count",
            "effect_snapshot_digest",
        ):
            if field in params:
                result[field] = params[field]
        return result

    def _receipt(
        self,
        plan: _Plan,
        job_id: str,
        started: datetime,
        *,
        state: str,
        effect_state: EffectState,
        evidence: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": job_id,
            "operation": f"security.{plan.action}.apply",
            "scope": self.scope.to_wire(),
            "plan_digest": str(plan.plan_digest),
            "state": state,
            "started_at": _timestamp(started),
            "finished_at": _timestamp(datetime.now(UTC)),
            "steps": [
                {
                    "id": "verify-and-apply",
                    "title": f"Verify and apply {plan.action}",
                    "effect": (
                        "delete_authoritative"
                        if plan.action == "purge"
                        else (
                            "write_authoritative"
                            if plan.action in {"restore", "tombstone"}
                            else "read"
                        )
                    ),
                    "state": "committed" if state == "committed" else "unknown",
                }
            ],
            "rollback_available": False,
            "effect_state": effect_state.value,
            "error": error,
        }
        if evidence:
            result.update(evidence)
        return result

    def _discard_plan(self, plan: _Plan) -> None:
        self._plans.pop(plan.plan_id, None)
        self._remove_plan(plan.plan_id)
        if plan.staging_id is not None and plan.memory_batch is None:
            self.lifecycle.discard(plan.staging_id)

    def _require_scope(self, scope: Scope) -> None:
        if scope != self.scope:
            raise SecurityOperationsError(
                "AUTHORIZATION_DENIED", "authenticated scope does not own this store"
            )

    def _memory(self) -> MemoryClient:
        if self.memory_client is None or self.memory_client.scope != self.scope:
            raise SecurityOperationsError(
                "MEMORY_PORTABILITY_UNAVAILABLE",
                "authenticated memory portability service is unavailable",
            )
        return self.memory_client


def service_for(owner: object) -> SecurityOperations:
    service = getattr(owner, "security_operations", None)
    if isinstance(service, SecurityOperations):
        return service
    raise SecurityOperationsError(
        "NOT_CONFIGURED", "Hypermid security operations are not configured"
    )


def _binding_digest(handle: SecretHandle) -> str:
    return hashlib.sha256(
        b"hypermid.secret.handle-binding.v1\0" + handle.identifier.encode()
    ).hexdigest()


def _memory_request(
    scope: Scope, operation: MemoryOperation, suffix: str
) -> MutationRequest:
    token = secrets.token_hex(10)
    return MutationRequest(
        operation=operation,
        actor_scope=scope,
        target_scope=scope,
        revision=RevisionPrecondition.must_not_exist(),
        trace=Trace(
            Id(f"security-{suffix}-trace-{token}"),
            Id(f"security-{suffix}-request-{token}"),
        ),
    )


def _bundle_identity(bundle: MemoryExportBundle) -> dict[str, Any]:
    manifest = bundle.manifest
    return {
        "export_id": str(manifest.export_id),
        "stream_digest": str(manifest.stream_digest),
        "bundle_digest": str(Digest.sha256(bundle.to_jsonl())),
        "item_count": manifest.item_count,
        "record_count": manifest.record_count,
        "cursor": manifest.cursor.to_wire(),
    }


def _backup_content_identity(bundle: MemoryExportBundle) -> dict[str, Any]:
    manifest = bundle.manifest
    return {
        "stream_digest": str(manifest.stream_digest),
        "item_count": manifest.item_count,
        "record_count": manifest.record_count,
        "cursor": manifest.cursor.to_wire(),
    }


def _backup_authority_digest(bundle: MemoryExportBundle) -> Digest:
    return Digest.sha256(_canonical(_backup_content_identity(bundle)))


def _source_evidence(plan: _Plan) -> dict[str, Any]:
    if plan.action != "restore":
        return {}
    evidence: dict[str, Any] = {}
    if plan.source_digest is not None:
        evidence["artifact_digest"] = str(plan.source_digest)
    for field in (
        "export_id",
        "stream_digest",
        "bundle_digest",
        "item_count",
        "record_count",
        "batch_source_digest",
        "credential_ref",
        "cursor",
    ):
        if field in plan.params:
            evidence[field if field != "bundle_digest" else "source_digest"] = (
                plan.params[field]
            )
    if plan.staging_id is not None:
        evidence["batch_id"] = plan.staging_id
    return evidence


def _database_digest(path: Path) -> Digest:
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
        return Digest.sha256(snapshot.serialize())
    finally:
        snapshot.close()
        source.close()


def _scope_cursor(path: Path, scope: Scope) -> Cursor:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _scope_cursor_connection(connection, scope)
    finally:
        connection.close()


def _scope_cursor_connection(connection: sqlite3.Connection, scope: Scope) -> Cursor:
    row = connection.execute(
        "SELECT epoch,sequence FROM memory_scopes WHERE scope_digest=?",
        (str(scope_digest(scope)),),
    ).fetchone()
    if row is None:
        raise SecurityOperationsError("SCOPE_NOT_FOUND", "memory scope is absent")
    return Cursor(int(row[0]), int(row[1]))


def _absolute_path(value: object, name: str) -> Path:
    raw = _text(value, name, 4096)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SecurityOperationsError("INVALID_PATH", f"{name} must be absolute")
    if path.is_symlink():
        raise SecurityOperationsError(
            "INVALID_PATH", f"{name} cannot be a symbolic link"
        )
    return path.resolve(strict=False)


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SecurityOperationsError("INVALID_REQUEST", f"{name} is invalid")
    return value


def _digest(value: object, name: str) -> Digest:
    raw = _text(value, name, 64)
    if _DIGEST.fullmatch(raw) is None:
        raise SecurityOperationsError("INVALID_REQUEST", f"{name} is invalid")
    return Digest(raw)


def _id(value: object, name: str) -> Id:
    raw = _text(value, name, 160)
    try:
        return Id(raw)
    except (TypeError, ValueError) as error:
        raise SecurityOperationsError(
            "INVALID_REQUEST", f"{name} is invalid"
        ) from error


def _uint(value: object, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 2**53 - 1
    ):
        raise SecurityOperationsError("INVALID_REQUEST", f"{name} is invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(UTC)


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal path is invalid"
        )
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal owner is invalid"
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal permissions are invalid"
        )


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    _private_directory(path.parent)
    encoded = _canonical(value)
    if len(encoded) > 8 * 1024 * 1024:
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal entry is too large"
        )
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_private_json(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or (hasattr(os, "geteuid") and info.st_uid != os.geteuid())
        or info.st_size > 8 * 1024 * 1024
    ):
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal entry is invalid"
        )
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal entry is unreadable"
        ) from error
    if not isinstance(value, dict):
        raise SecurityOperationsError(
            "SECURITY_JOURNAL_INVALID", "security journal entry is invalid"
        )
    return value


@contextmanager
def _exclusive_journal_lock(path: Path):
    _private_directory(path.parent)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or (hasattr(os, "geteuid") and info.st_uid != os.geteuid())
        ):
            raise SecurityOperationsError(
                "SECURITY_JOURNAL_INVALID", "security journal lock is invalid"
            )
        if os.name == "nt":
            import msvcrt

            windows_lock = cast(_WindowsFileLock, msvcrt)

            if info.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                windows_lock.locking(descriptor, windows_lock.LK_NBLCK, 1)
            except OSError as error:
                raise SecurityOperationsError(
                    "PLAN_BUSY", "security plan is already being applied"
                ) from error
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                windows_lock.locking(descriptor, windows_lock.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SecurityOperationsError(
                    "PLAN_BUSY", "security plan is already being applied"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


__all__ = ["SecurityOperations", "SecurityOperationsError", "service_for"]
