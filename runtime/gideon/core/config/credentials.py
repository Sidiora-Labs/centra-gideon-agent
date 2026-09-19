"""Credential backend selection and indexed keychain or atomic dotenv persistence."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from gideon.core.config import loader as _loader

logger = logging.getLogger(__name__)
CredentialBackend = Literal["keychain", "dotenv"]
CREDENTIAL_BACKEND_ENV = "GIDEON_CREDENTIAL_BACKEND"
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
        replacement = f"{key}={value}"
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
            values[key] = line.split("=", 1)[1].strip()
    return values


def _usable_keyring() -> object | None:
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
    return requested if requested == "keychain" and keychain_available() else "dotenv"


def credential_backend_warning() -> str:
    if requested_credential_backend() != "keychain" or credential_backend() != "dotenv":
        return ""
    return "keychain requested but no usable OS keyring backend is available — credentials stay in .env at mode 0600 (never plaintext elsewhere)"


class KeyringStore:
    def __init__(self, backend):
        self.backend = backend

    def index(self) -> list[str]:
        try:
            raw = self.backend.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_INDEX_KEY)
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
        try:
            return self.backend.get_password(_KEYCHAIN_SERVICE, key) or ""
        except Exception:
            logger.debug("keychain read failed for %s", key, exc_info=True)
            return ""

    def put(self, key: str, value: str) -> bool:
        try:
            self.backend.set_password(_KEYCHAIN_SERVICE, key, value)
            names = _keychain_index()
            if key not in names:
                self.backend.set_password(
                    _KEYCHAIN_SERVICE,
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
        failures = []
        try:
            self.backend.delete_password(_KEYCHAIN_SERVICE, key)
        except Exception:
            if _keychain_get(key):
                logger.warning("keychain delete failed for %s", key)
                failures.append("entry")
        try:
            names = _keychain_index()
            if key in names:
                retained = sorted(name for name in names if name != key)
                self.backend.set_password(
                    _KEYCHAIN_SERVICE, _KEYCHAIN_INDEX_KEY, json.dumps(retained)
                )
        except Exception:
            logger.warning("keychain index update failed after deleting %s", key)
            failures.append("index")
        return not failures


def _keychain_index() -> list[str]:
    backend = _usable_keyring()
    return KeyringStore(backend).index() if backend is not None else []


def _keychain_get(key: str) -> str:
    backend = _usable_keyring()
    return KeyringStore(backend).get(key) if backend is not None else ""


def _keychain_credentials() -> dict[str, str]:
    return {key: value for key in _keychain_index() if (value := _keychain_get(key))}


def _keychain_save(key: str, value: str) -> bool:
    backend = _usable_keyring()
    return KeyringStore(backend).put(key, value) if backend is not None else False


def _keychain_delete(key: str) -> bool:
    backend = _usable_keyring()
    return KeyringStore(backend).remove(key) if backend is not None else False


def _dotenv_remove_credentials(keys: Iterable[str]) -> list[str]:
    return DotenvDocument(_loader.env_path()).remove(keys)


def _dotenv_save_credential(key: str, value: str) -> None:
    DotenvDocument(_loader.env_path()).upsert(key, value)


def _dotenv_credentials() -> dict[str, str]:
    return DotenvDocument(_loader.env_path()).values()


def _dotenv_names() -> list[str]:
    return DotenvDocument(_loader.env_path()).names()


def save_credential(key: str, value: str) -> None:
    stored = credential_backend() == "keychain" and _keychain_save(key, value)
    if not stored:
        _dotenv_save_credential(key, value)
    os.environ[key] = value


def get_credential(key: str) -> str:
    return _keychain_get(key) or _dotenv_credentials().get(key, "")


def credential_names() -> list[str]:
    return sorted(set(_keychain_index()).union(_dotenv_names()))


def delete_credential(key: str) -> bool:
    existed = key in credential_names() or key in os.environ
    if _usable_keyring() is not None:
        _keychain_delete(key)
    _dotenv_remove_credentials((key,))
    os.environ.pop(key, None)
    return existed
