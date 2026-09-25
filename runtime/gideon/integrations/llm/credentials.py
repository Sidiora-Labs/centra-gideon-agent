"""Resolve named credentials and publish private descriptor snapshots."""

import fcntl
import json
import logging
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
CredentialKind = Literal["none", "api_key", "static_token", "oauth2"]
CredentialSource = Literal["env", "file", "none"]
_SECRET_BEARING_KINDS = frozenset({"api_key", "static_token", "oauth2"})


@dataclass(frozen=True)
class Credential:
    name: str
    kind: CredentialKind
    secret: str | None = None
    source: CredentialSource = "none"


class CredentialStore:
    CREDENTIALS_FILE = "credentials.json"
    ENV_FILE = ".env"
    FILE_MODE = 0o600

    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._credentials_path = self._home / self.CREDENTIALS_FILE
        self._env_path = self._home / self.ENV_FILE
        self._state_lock = threading.RLock()
        self._descriptors: dict[str, dict[str, object]] = {}
        self._env: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        with self._state_lock:
            descriptors, environment = self._load_descriptors(), self._load_env_file()
            self._descriptors, self._env = descriptors, environment

    def has(self, name: str) -> bool:
        with self._state_lock:
            return name in self._descriptors

    def list(self) -> list[Credential]:
        with self._state_lock:
            return [
                replace(self.resolve(name), secret=None) for name in self._descriptors
            ]

    def resolve(self, name: str) -> Credential:
        with self._state_lock:
            descriptor = self._descriptors[name]
            kind = str(descriptor.get("type", "none"))
            if kind not in _SECRET_BEARING_KINDS:
                if kind != "none":
                    logger.warning("Credential %r has unsupported kind %r", name, kind)
                return Credential(name, "none")
            credential_kind = cast(CredentialKind, kind)
            reference = descriptor.get("value_env")
            candidates: tuple[tuple[CredentialSource, object], ...] = (
                (
                    "env",
                    (
                        os.environ.get(reference)
                        if isinstance(reference, str) and reference
                        else None
                    ),
                ),
                ("file", descriptor.get("value")),
                ("file", self._env.get(name)),
            )
            for origin, value in candidates:
                if isinstance(value, str) and value:
                    return Credential(name, credential_kind, value, origin)
            return Credential(name, credential_kind)

    def save(self, descriptors: dict[str, dict[str, object]]) -> None:
        snapshot = {name: dict(row) for name, row in descriptors.items()}
        payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
        with self._state_lock:
            atomic_write(
                self._credentials_path, payload, fsync=True, mode=self.FILE_MODE
            )
            self._descriptors = snapshot

    def put(self, name: str, descriptor: dict[str, object]) -> None:
        if not isinstance(name, str) or not name or not isinstance(descriptor, dict):
            raise ValueError("Credential name and descriptor required")
        self._mutate(name, dict(descriptor))

    def remove(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Credential name required")
        self._mutate(name, None)

    def _mutate(self, name: str, descriptor: dict[str, object] | None) -> None:
        with self._state_lock:
            self._home.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._home / ".credentials.lock", os.O_CREAT | os.O_RDWR, self.FILE_MODE)
            with os.fdopen(fd, "a+") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    snapshot = self._load_descriptors()
                    if descriptor is None:
                        snapshot.pop(name, None)
                    else:
                        snapshot[name] = descriptor
                    self.save(snapshot)
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _private_text(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        self._enforce_perms(path)
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            logger.warning("Cannot read credential file %s", path)
            return None

    def _load_descriptors(self) -> dict[str, dict[str, object]]:
        text = self._private_text(self._credentials_path)
        if text is None:
            return {}
        try:
            document = json.loads(text)
        except ValueError:
            logger.warning("Credential descriptors are malformed")
            return {}
        if not isinstance(document, dict):
            logger.warning("Credential descriptors must be a JSON object")
            return {}
        accepted = {}
        for name, row in document.items():
            if not isinstance(row, dict):
                logger.warning("Ignoring non-object credential descriptor %r", name)
                continue
            accepted[str(name)] = dict(row)
        return accepted

    def _load_env_file(self) -> dict[str, str]:
        environment = {}
        for line in (self._private_text(self._env_path) or "").splitlines():
            line = line.strip()
            key, separator, value = line.partition("=")
            if separator and not line.startswith("#"):
                environment[key.strip()] = value.strip()
        return environment

    def _enforce_perms(self, path: Path) -> None:
        try:
            if path.stat().st_mode & 0o077:
                os.chmod(path, self.FILE_MODE)
        except OSError:
            logger.warning("Cannot enforce private permissions on %s", path)
