"""Owner-bound references for secrets persisted in the core config document."""

from __future__ import annotations

import base64
import copy
import hashlib
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gideon.core.config import credentials, document as config_document

logger = logging.getLogger(__name__)
_PREFIX = credentials.CONFIG_SECRET_REFERENCE_PREFIX
_REF = re.compile(r"^gideon-config-secret:v1:([A-Za-z0-9_-]+):([0-9a-f]{32})$")


class ConfigSecretReferenceError(ValueError):
    """A config secret reference is malformed, missing, or owned by another field."""


@dataclass(frozen=True, slots=True)
class ConfigSecretCleanupResult:
    status: str
    removed_count: int = 0
    failure_count: int = 0


def _encode_owner(owner: str) -> str:
    return base64.urlsafe_b64encode(owner.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_owner(encoded: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        owner = raw.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ConfigSecretReferenceError("malformed config secret reference") from exc
    if not owner.startswith("/") or _encode_owner(owner) != encoded:
        raise ConfigSecretReferenceError("malformed config secret reference")
    return owner


def _parse_reference(value: str) -> tuple[str, str] | None:
    if not value.startswith(_PREFIX):
        return None
    match = _REF.fullmatch(value)
    if match is None:
        raise ConfigSecretReferenceError("malformed config secret reference")
    return _decode_owner(match.group(1)), value


def _escape(part: object) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = path + (_escape(key),)
            if config_document._credential_field(key) and isinstance(item, (dict, list)):
                yield "/" + "/".join(child_path), item, str(key)
            else:
                yield from _walk(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, path + (str(index),))
    else:
        yield "/" + "/".join(path), value, path[-1] if path else ""


def _set_path(root: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current: Any = root
    for raw in path[:-1]:
        part = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    last = path[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def _pointer_parts(path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in path)


def _is_provider_connection_locator(path: tuple[str, ...]) -> bool:
    parts = _pointer_parts(path)
    return len(parts) == 3 and parts[0] == "provider_connections" and bool(parts[1]) and parts[2] == "credential_ref"


def _is_credential_keychain_policy(path: tuple[str, ...]) -> bool:
    return _pointer_parts(path) == ("security", "credential_keychain")


def _validate_credential_keychain_policy(value: Any) -> None:
    if type(value) is not bool:
        raise ConfigSecretReferenceError("credential keychain policy must be a boolean")


def _validate_provider_connection_locator(value: Any) -> Any:
    if value is None or value == "":
        return value
    if not isinstance(value, str) or value.startswith("gideon-"):
        raise ConfigSecretReferenceError("provider connection locator is invalid")
    from gideon.core.config.loader import config_dir
    from gideon.integrations.llm.credentials import CredentialStore

    if not CredentialStore(config_dir()).has(value):
        raise ConfigSecretReferenceError("provider connection credential locator is missing")
    return value


def _reference_at(value: Any, owner: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = _parse_reference(value)
    if parsed is None:
        return None
    if parsed[0] != owner:
        raise ConfigSecretReferenceError("config secret reference belongs to another field")
    if credentials.get_secret_value(value) == "":
        raise ConfigSecretReferenceError("config secret reference is missing")
    return value


def _scan_stray_references(value: Mapping[str, Any], secret_owners: set[str]) -> None:
    for owner, leaf, _name in _walk(value):
        if isinstance(leaf, str) and leaf.startswith(_PREFIX) and owner not in secret_owners:
            raise ConfigSecretReferenceError("config secret reference is outside a credential field")


class ConfigSecretPlan:
    def __init__(self, stored: dict[str, Any], staged: list[str], superseded: list[str]):
        self.document = stored
        self._staged = staged
        self._superseded = superseded
        self._finished = False

    @staticmethod
    def _remove(keys: list[str]) -> tuple[int, int]:
        removed = failures = 0
        for key in keys:
            try:
                if credentials.delete_secret_value(key):
                    removed += 1
            except Exception:
                failures += 1
        return removed, failures

    def abort(self) -> ConfigSecretCleanupResult:
        if self._finished:
            return ConfigSecretCleanupResult("aborted")
        self._finished = True
        removed, failures = self._remove(self._staged)
        if failures:
            logger.warning("config secret cleanup incomplete after aborted write (%d failures)", failures)
        return ConfigSecretCleanupResult(
            "abort_cleanup_incomplete" if failures else "aborted", removed, failures
        )

    def commit(self) -> ConfigSecretCleanupResult:
        if self._finished:
            return ConfigSecretCleanupResult("complete")
        self._finished = True
        removed, failures = self._remove(self._superseded)
        if failures:
            logger.warning("config secret cleanup incomplete after committed write (%d failures)", failures)
        return ConfigSecretCleanupResult(
            "cleanup_incomplete" if failures else "complete", removed, failures
        )


def prepare_config_secrets(
    document: dict[str, Any], *, previous: Mapping[str, Any] | None
) -> ConfigSecretPlan:
    """Return a reference-only config copy and a staged, reversible store plan."""
    if not isinstance(document, dict):
        raise TypeError("config document must be an object")
    old = previous if isinstance(previous, Mapping) else {}
    stored = copy.deepcopy(document)
    old_leaves = {owner: (value, name, path) for owner, value, name, path in _walk_with_paths(old)}
    staged: list[str] = []
    superseded: set[str] = set()
    secret_owners: set[str] = set()
    try:
        for owner, value, name, path in list(_walk_with_paths(stored)):
            if _is_credential_keychain_policy(path):
                _validate_credential_keychain_policy(value)
                continue
            if _is_provider_connection_locator(path):
                locator = value
                if value == config_document.CREDENTIAL_MASK:
                    prior = old_leaves.get(owner)
                    if prior is None or prior[0] in (None, ""):
                        raise ConfigSecretReferenceError("masked provider locator has no stored value")
                    locator = prior[0]
                _set_path(stored, path, _validate_provider_connection_locator(locator))
                continue
            if not config_document._credential_field(name):
                continue
            secret_owners.add(owner)
            if isinstance(value, (dict, list)):
                raise ConfigSecretReferenceError("credential fields must contain scalar values")
            old_value = old_leaves.get(owner, (None, "", ()))[0]
            old_reference = _reference_at(old_value, owner)
            if value == config_document.CREDENTIAL_MASK:
                if old_reference is None:
                    raise ConfigSecretReferenceError("masked credential has no stored owner reference")
                replacement = old_reference
            elif value is None or value == "":
                replacement = value
            elif isinstance(value, str):
                current_reference = _reference_at(value, owner)
                if current_reference is not None:
                    replacement = current_reference
                elif value.startswith(_PREFIX):
                    raise ConfigSecretReferenceError("malformed config secret reference")
                elif old_reference is not None and credentials.get_secret_value(old_reference) == value:
                    replacement = old_reference
                else:
                    replacement = f"{_PREFIX}{_encode_owner(owner)}:{secrets.token_hex(16)}"
                    credentials.put_secret_value(replacement, value)
                    staged.append(replacement)
            else:
                raise ConfigSecretReferenceError("credential fields must contain strings")
            _set_path(stored, path, replacement)
            if old_reference is not None and replacement != old_reference:
                superseded.add(old_reference)
        _scan_stray_references(stored, secret_owners)
        old_refs: set[str] = set()
        for owner, (old_value, name, _path) in old_leaves.items():
            if _is_credential_keychain_policy(_path):
                _validate_credential_keychain_policy(old_value)
                continue
            if _is_provider_connection_locator(_path):
                _validate_provider_connection_locator(old_value)
                continue
            if not config_document._credential_field(name):
                continue
            reference = _reference_at(old_value, owner)
            if reference is not None:
                old_refs.add(reference)
        current_refs = {
            value
            for owner, value, name, _path in _walk_with_paths(stored)
            if not _is_credential_keychain_policy(_path)
            and config_document._credential_field(name)
            and isinstance(value, str)
            and value.startswith(_PREFIX)
        }
        superseded.update(old_refs - current_refs)
        _scan_stray_references(old, {owner for owner, (_value, name, _path) in old_leaves.items() if not _is_credential_keychain_policy(_path) and config_document._credential_field(name)})
    except BaseException:
        ConfigSecretPlan._remove(staged)
        raise
    return ConfigSecretPlan(stored, staged, sorted(superseded))


def _walk_with_paths(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = path + (_escape(key),)
            if config_document._credential_field(key) and isinstance(item, (dict, list)):
                yield "/" + "/".join(child_path), item, str(key), child_path
            else:
                yield from _walk_with_paths(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_with_paths(item, path + (str(index),))
    else:
        yield "/" + "/".join(path), value, path[-1].replace("~1", "/").replace("~0", "~") if path else "", path


def resolve_config_secrets(document: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve only references whose encoded owner matches their config pointer."""
    if not isinstance(document, Mapping):
        raise TypeError("config document must be an object")
    resolved = copy.deepcopy(dict(document))
    secret_owners: set[str] = set()
    for owner, value, name, path in list(_walk_with_paths(resolved)):
        if _is_credential_keychain_policy(path):
            _validate_credential_keychain_policy(value)
            continue
        if _is_provider_connection_locator(path):
            _set_path(resolved, path, _validate_provider_connection_locator(value))
            continue
        if not config_document._credential_field(name):
            continue
        secret_owners.add(owner)
        if isinstance(value, (dict, list)):
            raise ConfigSecretReferenceError("credential fields must contain scalar values")
        if isinstance(value, str) and value.startswith(_PREFIX):
            reference = _reference_at(value, owner)
            if reference is None:
                raise ConfigSecretReferenceError("malformed config secret reference")
            secret = credentials.get_secret_value(reference)
            if not secret:
                raise ConfigSecretReferenceError("config secret reference is missing")
            _set_path(resolved, path, secret)
        elif value not in (None, "") and not isinstance(value, str):
            raise ConfigSecretReferenceError("credential fields must contain strings")
    _scan_stray_references(resolved, secret_owners)
    return resolved


def document_module_credential_field(name: str) -> bool:
    return config_document._credential_field(name)


# Record-owned references used by app and provider settings. These helpers deliberately
# coexist with the core-config transaction mapper above: its prepare/commit protocol
# remains the authority for config.json writes.
_OWNER_REFERENCE = re.compile(r"^\{\{secret:([A-Za-z0-9_]+)\}\}$")
_OWNER_PREFIX = "GIDEON_SECRET"


class ForeignSecretReference(ValueError):
    """A settings record tried to resolve a value held by a different owner."""


@dataclass(frozen=True, slots=True)
class SecretOwner:
    kind: str
    name: str
    instance: str = ""

    @property
    def prefix(self) -> str:
        parts = [_OWNER_PREFIX, self.kind.upper(), _owner_segment(self.name)]
        if self.instance:
            parts.append(_owner_segment(self.instance))
        return "_".join(parts) + "__"

    def key(self, field: str) -> str:
        return self.prefix + _owner_segment(field) + "_" + secrets.token_hex(8).upper()

    def owns(self, key: str) -> bool:
        return key.startswith(self.prefix)


def _owner_segment(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()[:32].rstrip("_") or "X"
    return f"{clean}_{hashlib.sha256(value.encode()).hexdigest()[:10].upper()}"


def app_owner(name: str) -> SecretOwner:
    return SecretOwner("APP", name)


def provider_owner(name: str) -> SecretOwner:
    return SecretOwner("PROVIDER", name)


def instance_owner(name: str, instance: str) -> SecretOwner:
    return SecretOwner("INSTANCE", name, instance)


def _owned_reference(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _OWNER_REFERENCE.fullmatch(value)
    return match.group(1) if match else None


def _owned_secret_field(name: str, declared: set[str]) -> bool:
    return name in declared or config_document._credential_field(name)


def _audit_foreign(owner: SecretOwner, field: str, operation: str) -> None:
    logger.warning("foreign credential reference refused owner=%s field=%s operation=%s", owner.kind.lower(), field, operation)
    try:
        from gideon.security.sel import sel
        sel().log_api_access(caller=f"{owner.kind.lower()}:{owner.name}", operation=operation,
                             outcome="denied", source="secret_refs", resources="credential-reference",
                             error="credential reference belongs to another owner")
    except Exception:
        logger.debug("credential ownership audit unavailable", exc_info=True)


def store(values: dict[str, Any], *, owner: SecretOwner, declared: set[str] | None = None,
          previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store secret fields and return a record containing owner-bound references only."""
    if not isinstance(values, dict):
        raise ValueError("settings must be an object")
    declared, previous = set(declared or ()), previous or {}
    result = dict(values)
    for field, value in values.items():
        if not _owned_secret_field(str(field), declared):
            continue
        old_key = _owned_reference(previous.get(field))
        new_key = _owned_reference(value)
        if value in (None, ""):
            if old_key and not owner.owns(old_key):
                _audit_foreign(owner, str(field), "clear")
                raise ForeignSecretReference("credential reference belongs to another owner")
            result[field] = value
            continue
        if new_key is not None:
            if not owner.owns(new_key) or not credentials.get_secret_value(new_key):
                _audit_foreign(owner, str(field), "store")
                raise ForeignSecretReference("credential reference belongs to another owner")
            result[field] = value
            continue
        if not isinstance(value, str):
            raise ValueError("credential fields must contain strings")
        if old_key and owner.owns(old_key) and credentials.get_secret_value(old_key) == value:
            result[field] = "{{secret:" + old_key + "}}"
            continue
        key = owner.key(str(field))
        credentials.put_secret_value(key, value)
        if credentials.get_secret_value(key) != value:
            raise OSError("credential backend did not verify the stored value")
        result[field] = "{{secret:" + key + "}}"
    return result


def resolve(values: dict[str, Any], *, owner: SecretOwner) -> dict[str, Any]:
    """Resolve references only when their key is in this record's owner namespace."""
    if not isinstance(values, dict):
        raise ValueError("settings must be an object")
    result = dict(values)
    for field, value in values.items():
        key = _owned_reference(value)
        if key is None:
            continue
        if not owner.owns(key):
            _audit_foreign(owner, str(field), "resolve")
            raise ForeignSecretReference("credential reference belongs to another owner")
        secret = credentials.get_secret_value(key)
        if not secret:
            raise ValueError("owned credential reference is missing")
        result[field] = secret
    return result


def purge(prefixes: list[str] | tuple[str, ...]) -> int:
    """Delete exactly the values within the supplied owner namespaces."""
    removed = 0
    for key in credentials.credential_names():
        if key.startswith(tuple(prefixes)) and credentials.delete_secret_value(key):
            removed += 1
    return removed


def purge_unused(owner: SecretOwner, values: dict[str, Any]) -> int:
    retained = {_owned_reference(value) for value in values.values()}
    removed = 0
    for key in credentials.credential_names():
        if owner.owns(key) and key not in retained and credentials.delete_secret_value(key):
            removed += 1
    return removed


def resolve_native_tool_secret(name: str) -> str:
    """Resolve a user-named native shell secret, never a gateway or owner-bound secret."""
    from gideon.core.config.credentials import credential_names, get_credential
    from gideon.security.secrets_vault import is_reserved_key

    if not isinstance(name, str) or not name or is_reserved_key(name):
        return ""
    try:
        if name not in credential_names():
            return ""
        return get_credential(name)
    except Exception:  # noqa: BLE001 - an unavailable store must fail closed
        logger.debug("native tool credential lookup failed for %r", name, exc_info=True)
        return ""
