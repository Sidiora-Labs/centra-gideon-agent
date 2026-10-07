from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeVar

from .foundation import Id, Scope
from .memory_client import MemoryClient
from .network_policy import Redactor, SecretDenied, SecretHandle
from .security_operations import SecurityOperations

_BACKUP_OPERATION = "hypermid.backup"
_RESTORE_OPERATION = "hypermid.restore"
_OPERATIONS = frozenset({_BACKUP_OPERATION, _RESTORE_OPERATION})
_PORTABILITY_RESOURCE = Id("memory-portability")
_REQUIRED_CAPABILITY_OPERATIONS = frozenset({"append", "export"})
T = TypeVar("T")


class _Credential(Protocol):
    @property
    def secret(self) -> str | None: ...


class _CredentialStore(Protocol):
    def resolve(self, name: str) -> _Credential: ...


@dataclass(frozen=True, slots=True)
class _Binding:
    principal_id: str
    operation: str
    credential_name: str


class CredentialStoreSecretVault:
    """Resolve owner and purpose-bound opaque handles from Gideon's credential store."""

    def __init__(self, store: _CredentialStore, *, scope: Scope) -> None:
        self._store = store
        self._scope = scope
        self._bindings: dict[str, _Binding] = {}

    def __repr__(self) -> str:
        return f"CredentialStoreSecretVault(bound={len(self._bindings)})"

    def bind(
        self, *, principal_id: str, operation: str, credential_name: str
    ) -> SecretHandle:
        if not principal_id or operation not in _OPERATIONS or not credential_name:
            raise SecretDenied("secret.invalid")
        material = json.dumps(
            {
                "scope": self._scope.to_wire(),
                "principal_id": principal_id,
                "operation": operation,
                "credential_name": credential_name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        identifier = (
            "hypermid_credential_"
            + hashlib.sha256(b"hypermid.credential.handle.v1\0" + material).hexdigest()
        )
        self._bindings[identifier] = _Binding(principal_id, operation, credential_name)
        return SecretHandle(identifier)

    def dispatch_with_secret(
        self,
        handle: SecretHandle,
        *,
        principal_id: str,
        operation: str,
        dispatch: Callable[[bytes], T],
    ) -> T:
        binding = self._bindings.get(handle.identifier)
        if binding is None:
            raise SecretDenied("secret.unknown")
        if (
            binding.principal_id != principal_id
            or binding.operation != operation
            or operation not in _OPERATIONS
        ):
            raise SecretDenied("secret.scope")
        return dispatch(self._material(binding.credential_name))

    def revoke(self, handle: SecretHandle) -> bool:
        return self._bindings.pop(handle.identifier, None) is not None

    def redactor(self) -> Redactor:
        values: list[bytes] = []
        for credential_name in {
            binding.credential_name for binding in self._bindings.values()
        }:
            try:
                values.append(self._material(credential_name))
            except SecretDenied:
                continue
        return Redactor(values)

    def _material(self, credential_name: str) -> bytes:
        try:
            secret = self._store.resolve(credential_name).secret
        except (KeyError, OSError, ValueError) as error:
            raise SecretDenied("secret.unknown") from error
        if not isinstance(secret, str) or not secret:
            raise SecretDenied("secret.unknown")
        try:
            material = base64.b64decode(secret.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as error:
            raise SecretDenied("secret.invalid") from error
        if len(material) != 32:
            raise SecretDenied("secret.invalid")
        return material


def local_backup_credential_name(scope: Scope) -> str:
    owner_digest = hashlib.sha256(
        b"hypermid.backup.credential.owner.v1\0" + str(scope.owner_id).encode("utf-8")
    ).hexdigest()[:24]
    return f"hypermid-backup-key-{owner_digest}"


def attach_security_operations(
    lifecycle: object, *, credential_store: _CredentialStore | None = None
) -> SecurityOperations | None:
    existing = getattr(lifecycle, "security_operations", None)
    if isinstance(existing, SecurityOperations):
        return existing
    adapter = getattr(lifecycle, "adapter", None)
    client = getattr(adapter, "client", None)
    enrollment = getattr(lifecycle, "enrollment", None)
    capability_id = getattr(lifecycle, "capability_id", None)
    if client is None or enrollment is None or capability_id is None:
        return None
    scope = client.scope
    enrollment.require_live(scope)
    operations = frozenset(getattr(enrollment, "operations", ()))
    resources = frozenset(getattr(enrollment, "resources", ()))
    if (
        not _REQUIRED_CAPABILITY_OPERATIONS.issubset(operations)
        or _PORTABILITY_RESOURCE not in resources
    ):
        return None

    if credential_store is None:
        from gideon.core.config.loader import config_dir
        from gideon.integrations.llm.credentials import CredentialStore

        credential_store = CredentialStore(config_dir())
    principal_id = str(enrollment.credential_id)
    credential_name = local_backup_credential_name(scope)
    vault = CredentialStoreSecretVault(credential_store, scope=scope)
    handles: Mapping[str, SecretHandle] = MappingProxyType(
        {
            "backup": vault.bind(
                principal_id=principal_id,
                operation=_BACKUP_OPERATION,
                credential_name=credential_name,
            ),
            "restore": vault.bind(
                principal_id=principal_id,
                operation=_RESTORE_OPERATION,
                credential_name=credential_name,
            ),
        }
    )
    record = Path(getattr(lifecycle, "connection_record"))
    service = SecurityOperations(
        scope=scope,
        principal_id=principal_id,
        active_path=record.parent / "state" / "memory.sqlite3",
        secret_vault=vault,
        supported_schema=1,
        memory_client=MemoryClient(client, capability_id=capability_id),
        credential_handles=handles,
    )
    setattr(lifecycle, "security_operations", service)
    setattr(
        lifecycle,
        "security_credential_handles",
        MappingProxyType({name: handle.identifier for name, handle in handles.items()}),
    )
    handlers = getattr(lifecycle, "handlers", None)
    if handlers is not None:
        setattr(handlers, "security_operations", service)
    return service


__all__ = [
    "CredentialStoreSecretVault",
    "attach_security_operations",
    "local_backup_credential_name",
]
