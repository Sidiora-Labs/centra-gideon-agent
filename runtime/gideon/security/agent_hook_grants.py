"""Owner grants for exact executable hook bytes, event, and matcher."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from gideon.core.atomic_write import atomic_write_bytes
from gideon.security.owner_grants import GrantBook, seal
from gideon.security.owner_only import gideon_home

BOOK = GrantBook("agent_hooks")
PINNED_DIR = ".allowed"


def key(event: str, command: str, matcher: str | None) -> str:
    """Identify a hook by its event, configured command, and matcher."""
    return f"{event}\0{command}\0{matcher or ''}"


def _read_command(command: str) -> bytes | None:
    try:
        path = Path(command)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


def seal_of(command: str) -> str:
    """Return the current file seal, or an empty string if it cannot be read safely."""
    content = _read_command(command)
    return seal(content) if content is not None else ""


def pinned_dir(*, home: str | os.PathLike[str] | None = None) -> Path:
    return gideon_home(home) / "hooks" / PINNED_DIR


def _ensure_pinned_dir(directory: Path) -> None:
    hooks_directory = directory.parent
    hooks_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(hooks_directory.lstat().st_mode):
        raise OSError("hook directory is not a directory")
    os.chmod(hooks_directory, 0o700)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise OSError("hook pin directory is not a directory")
    os.chmod(directory, 0o700)


def pinned(
    event: str,
    command: str,
    matcher: str | None,
    *,
    write: bool = True,
    home: str | os.PathLike[str] | None = None,
) -> str:
    """Return the private immutable-by-content copy that was granted, or empty."""
    content = _read_command(command)
    if content is None or not BOOK.holds(key(event, command, matcher), content):
        return ""
    digest = seal(content)
    target_dir = pinned_dir(home=home)
    target = target_dir / digest
    try:
        info = target.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode):
            return ""
        try:
            if target.read_bytes() != content:
                return ""
        except OSError:
            return ""
        return str(target)
    if not write:
        return ""
    try:
        _ensure_pinned_dir(target_dir)
        atomic_write_bytes(target, content, fsync=True, mode=0o700)
        if target.read_bytes() != content:
            return ""
    except OSError:
        return ""
    return str(target)


def allowed(event: str, command: str, matcher: str | None) -> bool:
    """Whether the owner allowed the file's exact current bytes for this hook identity."""
    return bool(pinned(event, command, matcher, write=False))


def allow(
    event: str,
    command: str,
    matcher: str | None,
    *,
    seen: str,
    principal: str = "",
) -> bool:
    """Grant only when the current file still matches the revision shown to the owner."""
    content = _read_command(command)
    if content is None or not seen or seal(content) != seen:
        return False
    BOOK.give(key(event, command, matcher), content, principal=principal)
    return True
