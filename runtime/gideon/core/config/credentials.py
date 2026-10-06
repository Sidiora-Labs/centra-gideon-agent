"""Credential backend selection and indexed keychain or atomic dotenv persistence."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from collections.abc import Callable
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from gideon.core.config import loader as _loader

logger = logging.getLogger(__name__)
CredentialBackend = Literal["keychain", "dotenv"]
CREDENTIAL_BACKEND_ENV = "GIDEON_CREDENTIAL_BACKEND"
CONFIG_SECRET_REFERENCE_PREFIX = "gideon-config-secret:v1:"
_KEYCHAIN_SERVICE = "gideon"
_KEYCHAIN_INDEX_KEY = "__gideon_key_index__"
_UNUSABLE_KEYRING_BACKENDS = ("keyring.backends.fail.", "keyring.backends.null.")


class DotenvDocument:
    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def key(line: str) -> str | None:
        stripped = line.strip()
        separator = stripped.find("=")
        if not stripped or stripped.startswith("#") or separator < 0:
            return None
        return stripped[:separator].strip()

    def lines(self) -> list[str]:
        return self.path.read_text().splitlines() if self.path.exists() else []

    def values(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_mode & 0o077:
                self.path.chmod(0o600)
        except OSError:
            logger.warning("Cannot enforce permissions on %s", self.path)
        return parse_dotenv(self.path.read_text())

    def names(self) -> list[str]:
        try:
            return [key for line in self.lines() if (key := self.key(line))]
        except OSError:
            logger.debug(
                "credential .env unreadable while listing names", exc_info=True
            )
            return []

    def commit(self, lines: list[str]) -> None:
        from gideon.core.atomic_write import atomic_write

        body = "\n".join(lines)
        atomic_write(self.path, body + "\n" if body else "", mode=0o600, fsync=True)

    def upsert(self, key: str, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = self.lines()
        positions = [index for index, line in enumerate(lines) if self.key(line) == key]
        replacement = f"{key}={json.dumps(value)}"
        for index in positions:
            lines[index] = replacement
        if not positions:
            lines.append(replacement)
        self.commit(lines)

    def remove(self, keys: Iterable[str]) -> list[str]:
        if not self.path.exists():
            return []
        wanted = set(keys)
        kept, removed = [], []
        for line in self.lines():
            key = self.key(line)
            if key is not None and key in wanted:
                removed.append(key)
            else:
                kept.append(line)
        if removed:
            self.commit(kept)
        return removed


def parse_dotenv(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        key = DotenvDocument.key(line)
        if key is not None:
            value = line.split("=", 1)[1].strip()
            if value.startswith('"'):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    pass
                else:
                    if isinstance(decoded, str):
                        value = decoded
            values[key] = value
    return values


KEYCHAIN_NAMESPACE_FILE = "keychain_namespace"
_keychain_disabled = False


def keychain_off() -> Callable[[], None]:
    global _keychain_disabled
    previous = _keychain_disabled
    _keychain_disabled = True

    def restore() -> None:
        global _keychain_disabled
        _keychain_disabled = previous

    return restore


@dataclass(frozen=True)
class KeychainNamespace:
    service: str
    scope: Literal["default", "own", "unnamed", "unreadable"]


def keychain_namespace(home: Path | None = None, *, mint: bool = False) -> KeychainNamespace:
    try:
        base = Path(home) if home is not None else _loader.resolve_config_dir()
        if base.resolve() == _loader.default_config_dir().resolve():
            return KeychainNamespace(_KEYCHAIN_SERVICE, "default")
        path = base / KEYCHAIN_NAMESPACE_FILE
        if mint and not path.exists():
            from gideon.core.atomic_write import atomic_write

            base.mkdir(parents=True, exist_ok=True)
            fresh = uuid.uuid4().hex
            staged = base / f".{KEYCHAIN_NAMESPACE_FILE}.{fresh}.tmp"
            try:
                atomic_write(staged, fresh + "\n", mode=0o600, fsync=True)
                try:
                    os.link(staged, path)
                except FileExistsError:
                    pass
            finally:
                staged.unlink(missing_ok=True)
        try:
            identifier = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return KeychainNamespace("", "unnamed")
        if re.fullmatch(r"[0-9a-f]{32}", identifier):
            return KeychainNamespace(f"{_KEYCHAIN_SERVICE}-{identifier}", "own")
    except (OSError, UnicodeError):
        logger.debug("keychain namespace unreadable", exc_info=True)
    return KeychainNamespace("", "unreadable")


def keychain_service(home: Path | None = None, *, mint: bool = False) -> str:
    return keychain_namespace(home, mint=mint).service


def keychain_namespace_summary(namespace: KeychainNamespace) -> str:
    if namespace.scope == "default":
        return f"{namespace.service} — the default home's"
    if namespace.scope == "own":
        return f"{namespace.service} — this home's own"
    if namespace.scope == "unnamed":
        return "none yet — named when this home first stores a secret in the keychain"
    return (f"unreadable — restore the id in {KEYCHAIN_NAMESPACE_FILE}, or remove the file "
            "to start a new empty namespace; no keychain is used here")


def _usable_keyring() -> object | None:
    if _keychain_disabled:
        return None
    try:
        import keyring
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        logger.debug(
            "keyring is installed but no backend could be resolved", exc_info=True
        )
        return None
    identity = ".".join((type(backend).__module__, type(backend).__name__))
    return None if identity.startswith(_UNUSABLE_KEYRING_BACKENDS) else keyring


def keychain_available() -> bool:
    return bool(_usable_keyring() is not None)


def requested_credential_backend() -> CredentialBackend:
    requested = (os.environ.get(CREDENTIAL_BACKEND_ENV) or "").strip().lower()
    if requested == "keychain":
        return "keychain"
    if requested == "dotenv":
        return "dotenv"
    if requested:
        logger.warning(
            "%s=%r is not a credential backend (keychain|dotenv); falling back to the security.credential_keychain config gate",
            CREDENTIAL_BACKEND_ENV,
            requested,
        )
    try:
        enabled = _loader.AppConfig.load().security.credential_keychain
    except Exception:
        logger.debug("credential gate unreadable; using .env", exc_info=True)
        enabled = False
    return "keychain" if enabled else "dotenv"


def credential_backend() -> CredentialBackend:
    requested = requested_credential_backend()
    return requested if requested == "keychain" and keychain_available() and keychain_namespace().scope != "unreadable" else "dotenv"


def credential_backend_warning() -> str:
    if requested_credential_backend() != "keychain" or credential_backend() != "dotenv":
        return ""
    if keychain_available():
        return "keychain requested but " + keychain_namespace_summary(keychain_namespace())
    return "keychain requested but no usable OS keyring backend is available — credentials stay in .env at mode 0600 (never plaintext elsewhere)"


def is_config_secret_reference_key(key: str) -> bool:
    """Identify opaque config-secret keys that must never be projected to env."""
    return isinstance(key, str) and key.startswith(CONFIG_SECRET_REFERENCE_PREFIX)


class KeyringStore:
    def __init__(self, backend, home: Path | None = None):
        self.backend = backend
        self.home = home

    def index(self) -> list[str]:
        service = keychain_service(self.home)
        if not service:
            return []
        try:
            raw = self.backend.get_password(service, _KEYCHAIN_INDEX_KEY)
        except Exception:
            logger.debug("keychain index unreadable", exc_info=True)
            return []
        if not raw:
            return []
        try:
            values = json.loads(raw)
        except ValueError:
            logger.warning(
                "keychain key index is not valid JSON; treating the keychain as empty"
            )
            return []
        if not isinstance(values, list):
            return []
        names = map(str, values)
        return [name for name in names if name and name != _KEYCHAIN_INDEX_KEY]

    def get(self, key: str) -> str:
        service = keychain_service(self.home)
        if not service:
            return ""
        try:
            return self.backend.get_password(service, key) or ""
        except Exception:
            logger.debug("keychain read failed for %s", key, exc_info=True)
            return ""

    def put(self, key: str, value: str) -> bool:
        service = keychain_service(self.home, mint=True)
        if not service:
            return False
        try:
            self.backend.set_password(service, key, value)
            names = self.index()
            if key not in names:
                self.backend.set_password(
                    service,
                    _KEYCHAIN_INDEX_KEY,
                    json.dumps(sorted([*names, key])),
                )
        except Exception:
            logger.warning(
                "keychain write failed for %s; falling back to .env (0600)", key
            )
            return False
        return True

    def remove(self, key: str) -> bool:
        service = keychain_service(self.home)
        if not service:
            return keychain_namespace(self.home).scope == "unnamed"
        failures = []
        try:
            self.backend.delete_password(service, key)
        except Exception:
            if self.get(key):
                logger.warning("keychain delete failed for %s", key)
                failures.append("entry")
        try:
            names = self.index()
            if key in names:
                retained = sorted(name for name in names if name != key)
                self.backend.set_password(
                    service, _KEYCHAIN_INDEX_KEY, json.dumps(retained)
                )
        except Exception:
            logger.warning("keychain index update failed after deleting %s", key)
            failures.append("index")
        return not failures


def _keychain_index() -> list[str]:
    backend = _usable_keyring()
    return KeyringStore(backend).index() if backend is not None else []


def _keychain_get(key: str, home: Path | None = None) -> str:
    backend = _usable_keyring()
    return KeyringStore(backend, home).get(key) if backend is not None else ""


def _keychain_credentials() -> dict[str, str]:
    return {key: value for key in _keychain_index() if (value := _keychain_get(key))}


def _keychain_save(key: str, value: str, home: Path | None = None) -> bool:
    backend = _usable_keyring()
    return KeyringStore(backend, home).put(key, value) if backend is not None else False


def _keychain_delete(key: str, home: Path | None = None) -> bool:
    backend = _usable_keyring()
    return KeyringStore(backend, home).remove(key) if backend is not None else False


def _dotenv_remove_credentials(keys: Iterable[str]) -> list[str]:
    return DotenvDocument(_loader.env_path()).remove(keys)


def _dotenv_save_credential(key: str, value: str) -> None:
    DotenvDocument(_loader.env_path()).upsert(key, value)


def _dotenv_credentials() -> dict[str, str]:
    return DotenvDocument(_loader.env_path()).values()


def _dotenv_names() -> list[str]:
    return DotenvDocument(_loader.env_path()).names()


def save_credential(key: str, value: str, home: Path | None = None) -> None:
    stored = credential_backend() == "keychain" and _keychain_save(key, value, home)
    if not stored:
        DotenvDocument(Path(home) / ".env" if home is not None else _loader.env_path()).upsert(key, value)
    if not is_config_secret_reference_key(key):
        from gideon.integrations.channel_transports import request_reconcile

        request_reconcile()


def get_credential(key: str, home: Path | None = None) -> str:
    document = DotenvDocument(Path(home) / ".env" if home is not None else _loader.env_path())
    return _keychain_get(key, home) or document.values().get(key, "")


def owner_id_credential(provider: str) -> str:
    """Return the credential key for one channel's owner identity.

    Provider names are part of the environment/credential key, so accept only the
    same portable characters used by registered transport names.
    """
    raw = str(provider or "").strip()
    if not raw or any(
        not (char.isascii() and (char.isalnum() or char in "-_"))
        for char in raw
    ):
        raise ValueError("provider must contain only letters, digits, hyphens, or underscores")
    normalized = "_".join(f"{byte:02X}" for byte in raw.encode("ascii"))
    return f"GIDEON_OWNER_ID_{normalized}"


def owner_id_for(provider: str) -> str:
    """Read a channel-specific owner id, falling back to the legacy shared key."""
    key = owner_id_credential(provider)
    value = os.environ.get(key, "").strip() or get_credential(key).strip()
    if value:
        return value
    return (
        os.environ.get(_loader.CRED_OWNER_ID, "").strip()
        or get_credential(_loader.CRED_OWNER_ID).strip()
    )


def put_secret_value(key: str, value: str) -> None:
    """Store an owner-scoped config secret without projecting it into the environment."""
    if credential_backend() == "keychain" and _keychain_save(key, value):
        return
    _dotenv_save_credential(key, value)


def get_secret_value(key: str) -> str:
    """Read an owner-scoped config secret without consulting process environment."""
    return _keychain_get(key) or _dotenv_credentials().get(key, "")


def delete_secret_value(key: str) -> bool:
    """Delete an owner-scoped config secret without changing process environment."""
    existed = bool(_keychain_get(key) or key in _dotenv_names())
    failures = []
    if _usable_keyring() is not None:
        if _keychain_get(key) and not _keychain_delete(key):
            failures.append("keychain")
    try:
        _dotenv_remove_credentials((key,))
    except Exception:
        failures.append("dotenv")
    if failures:
        raise OSError("credential backend cleanup failed")
    return existed


def credential_names() -> list[str]:
    return sorted(set(_keychain_index()).union(_dotenv_names()))


def delete_credential(key: str, home: Path | None = None) -> bool:
    document = DotenvDocument(Path(home) / ".env" if home is not None else _loader.env_path())
    existed = bool(_keychain_get(key, home)) or key in document.names() or key in os.environ
    if _usable_keyring() is not None:
        _keychain_delete(key, home)
    document.remove((key,))
    if home is None or Path(home).resolve() == _loader.resolve_config_dir().resolve():
        os.environ.pop(key, None)
    return existed
