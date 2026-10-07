"""Canonical paths reserved for owner-only agent operations."""

from __future__ import annotations

import os
import re
import shlex
import stat
from pathlib import Path

OWNER_ONLY_HOME_NAMES: tuple[str, ...] = ("hooks", "grants")
OWNER_ONLY_OPERATION_MESSAGE = "an agent may not access owner-only paths"

_PATH_TOKEN = re.compile(
    r"(?:[A-Za-z]:)?(?:/[^\s\"';&|<>]+|(?:\.\.?/|~/|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/)[^\s\"';&|<>]*|(?:\.\.?/)?(?:hooks|grants)(?:/[^\s\"';&|<>]*)?)"
)
_COMMAND_BREAK = frozenset({";", "&&", "||", "|", "\n"})


def gideon_home(home: str | os.PathLike[str] | None = None) -> Path:
    if home is not None:
        return Path(home).expanduser().resolve(strict=False)
    configured = os.environ.get("GIDEON_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    try:
        from gideon.core.config.loader import config_dir

        return Path(config_dir()).resolve(strict=False)
    except Exception:
        return (Path.home() / ".gideon").resolve(strict=False)


def owner_only_paths(home: str | os.PathLike[str] | None = None) -> tuple[Path, ...]:
    root = gideon_home(home)
    return tuple(root / name for name in OWNER_ONLY_HOME_NAMES)


def prepare_owner_only_paths(
    home: str | os.PathLike[str] | None = None,
) -> tuple[Path, ...]:
    """Create and validate the owner-state mountpoints before a child is spawned."""
    root = gideon_home(home)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise PermissionError(OWNER_ONLY_OPERATION_MESSAGE)
    paths = owner_only_paths(root)
    for path in paths:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise PermissionError(OWNER_ONLY_OPERATION_MESSAGE)
    return (root, *paths)


def owner_only_path_reason(
    path: str | os.PathLike[str],
    *,
    home: str | os.PathLike[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    raw = os.path.expandvars(os.path.expanduser(os.fspath(path).strip()))
    if not raw:
        return ""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (
            Path(cwd).expanduser() / candidate
            if cwd is not None
            else Path.cwd() / candidate
        )
    root = gideon_home(home)
    reserved = owner_only_paths(root)
    for value in (candidate, candidate.resolve(strict=False)):
        normalized = Path(os.path.normpath(str(value)))
        for protected in reserved:
            if normalized == protected or protected in normalized.parents:
                return OWNER_ONLY_OPERATION_MESSAGE
    return ""


def owner_only_command_reason(
    command: str,
    *,
    home: str | os.PathLike[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    """Refuse shell text that names reserved home paths without executing it."""
    source = str(command or "")
    if not source.strip():
        return ""
    root = gideon_home(home)
    expanded = source.replace("${GIDEON_HOME}", str(root)).replace(
        "$GIDEON_HOME", str(root)
    )
    expanded = expanded.replace("${HOME}", str(Path.home())).replace(
        "$HOME", str(Path.home())
    )
    if any(str(path) in expanded for path in owner_only_paths(root)):
        return OWNER_ONLY_OPERATION_MESSAGE
    current = (
        Path(cwd).expanduser().resolve(strict=False) if cwd is not None else Path.cwd()
    )
    lexer = shlex.shlex(expanded, posix=True, punctuation_chars=";&|<>\n")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = re.findall(r"[^\s;&|<>]+|&&|\|\|", expanded)

    segment: list[str] = []

    def check_segment(parts: list[str], base: Path) -> tuple[str, Path]:
        next_base = base
        if len(parts) >= 2 and parts[0] == "cd":
            target = Path(os.path.expanduser(parts[1]))
            next_base = (target if target.is_absolute() else base / target).resolve(
                strict=False
            )
        for token in parts:
            for match in _PATH_TOKEN.finditer(token):
                value = match.group(0).rstrip(",:)]}")
                reason = owner_only_path_reason(value, home=root, cwd=base)
                if reason:
                    return reason, next_base
        return "", next_base

    for token in tokens:
        if token in _COMMAND_BREAK:
            reason, current = check_segment(segment, current)
            if reason:
                return reason
            segment = []
        else:
            segment.append(token)
    reason, _ = check_segment(segment, current)
    return reason
