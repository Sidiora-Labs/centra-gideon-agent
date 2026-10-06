"""Safe, process-shared coordination for JSON record files."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

from gideon.core.atomic_write import atomic_write

_LOCK_NAME = ".gideon-record-files.lock"


def _store_root(root: Path | str, *, create: bool) -> Path:
    requested = Path(root)
    if requested.is_symlink():
        raise ValueError(f"record store root is a symlink: {requested}")
    if create:
        requested.mkdir(parents=True, exist_ok=True)
    elif not requested.is_dir():
        raise ValueError(f"record store root is not a directory: {requested}")
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise ValueError(f"record store root is not a directory: {resolved}")
    return resolved


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def locked_store(root: Path | str) -> Iterator[Path]:
    """Create and exclusively lock a record store; yield its canonical path.

    The lock lives in the store and is held only for local filesystem work. Every
    writer sharing a store must use this context before reading or changing records.
    """
    store = _store_root(root, create=True)
    lock_path = store / _LOCK_NAME
    if lock_path.is_symlink():
        raise ValueError(f"record store lock is a symlink: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"record store lock is not a regular file: {lock_path}")
        _lock(fd)
        try:
            yield store
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def safe_target(root: Path | str, relative: str) -> Path:
    """Resolve a canonical store-relative path and reject traversal or symlinks."""
    store = _store_root(root, create=False)
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or "\0" in relative
    ):
        raise ValueError("record path must be a non-empty canonical relative path")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or rel.as_posix() != relative:
        raise ValueError(f"unsafe record path: {relative!r}")
    if any(part in (".", "..") for part in rel.parts):
        raise ValueError(f"unsafe record path: {relative!r}")

    target = store.joinpath(*rel.parts)
    cursor = store
    for index, part in enumerate(rel.parts):
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"record path contains a symlink: {cursor}")
        if index < len(rel.parts) - 1 and cursor.exists() and not cursor.is_dir():
            raise ValueError(f"record path parent is not a directory: {cursor}")
        if index == len(rel.parts) - 1 and cursor.exists() and not cursor.is_file():
            raise ValueError(f"record destination is not a regular file: {cursor}")
    try:
        target.resolve(strict=False).relative_to(store)
    except ValueError as exc:
        raise ValueError(f"record path escapes its store: {relative!r}") from exc
    return target


def write_text_locked(root: Path | str, relative: str, content: str) -> bool:
    """Atomically write changed text beneath an already locked store.

    Returns false when the destination already contains the requested bytes.
    """
    store = _store_root(root, create=False)
    target = safe_target(store, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = safe_target(store, relative)
    encoded = content.encode("utf-8")
    if target.exists():
        if not target.is_file():
            raise ValueError(f"record destination is not a regular file: {target}")
        if target.read_bytes() == encoded:
            return False
    atomic_write(target, content)
    return True


def remove_locked(root: Path | str, relative: str) -> bool:
    """Remove a store-relative record beneath an already locked store."""
    target = safe_target(root, relative)
    if not target.exists():
        return False
    if not target.is_file():
        raise ValueError(f"record destination is not a regular file: {target}")
    target.unlink()
    return True
