"""Content-sealed owner grants for agent-authored unattended execution."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from gideon.core.atomic_write import atomic_json_write
from gideon.security.owner_only import gideon_home

logger = logging.getLogger(__name__)
_BOOK_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class GrantBookError(OSError):
    """A grant book could not be safely read or updated."""


def seal(content: str | bytes) -> str:
    """Return the SHA-256 seal for exact text or bytes."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


class GrantBook:
    """A private ``key -> content seal`` book; unreadable books grant nothing."""

    def __init__(self, name: str, *, home: str | os.PathLike[str] | None = None) -> None:
        if not _BOOK_NAME.fullmatch(name):
            raise ValueError("invalid grant book name")
        self.name = name
        self._home = home

    @property
    def path(self) -> Path:
        return gideon_home(self._home) / "grants" / f"{self.name}.json"

    def _read(self, *, strict: bool = False) -> dict[str, dict[str, str]]:
        path = self.path
        try:
            parent_info = path.parent.lstat()
            if not stat.S_ISDIR(parent_info.st_mode):
                raise GrantBookError("grant directory is not a directory")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise GrantBookError("grant book is not a regular file")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError) as exc:
            if strict:
                raise GrantBookError("grant book is unreadable") from exc
            logger.warning("grant book is unreadable; refusing all grants")
            return {}
        grants = raw.get("grants") if isinstance(raw, dict) else None
        if not isinstance(grants, dict):
            if strict:
                raise GrantBookError("grant book has invalid structure")
            return {}
        return {
            key: value
            for key, value in grants.items()
            if isinstance(key, str)
            and isinstance(value, dict)
            and isinstance(value.get("seal"), str)
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise GrantBookError("grant directory is not a directory")
        os.chmod(directory, 0o700)
        lock_path = directory / f".{self.name}.lock"
        fd = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise GrantBookError("grant lock is not a regular file")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _write(self, grants: dict[str, dict[str, str]]) -> None:
        path = self.path
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise GrantBookError("grant book is not a regular file")
        atomic_json_write(path, {"version": 1, "grants": grants}, replace_fallback=False)
        os.chmod(path, 0o600)

    def holds(self, key: str, content: str | bytes) -> bool:
        """Whether the current private book allows this exact content."""
        entry = self._read().get(key)
        return bool(entry and entry.get("seal") == seal(content))

    def give(
        self, key: str, content: str | bytes, *, principal: str = ""
    ) -> None:
        """Record owner consent; callers must establish owner authority and ask first."""
        if not isinstance(key, str) or not key:
            raise ValueError("grant key is required")
        if principal and (not isinstance(principal, str) or len(principal) > 240):
            raise ValueError("invalid grant principal")
        with self._locked():
            grants = self._read(strict=True)
            entry = {
                "seal": seal(content),
                "at": datetime.now(timezone.utc).isoformat(),
            }
            if principal:
                entry["principal"] = principal
            grants[key] = entry
            self._write(grants)

    def revoke(self, key: str) -> None:
        """Remove one grant without changing other valid records."""
        with self._locked():
            grants = self._read(strict=True)
            if grants.pop(key, None) is not None:
                self._write(grants)

    def keys(self) -> set[str]:
        return set(self._read())
