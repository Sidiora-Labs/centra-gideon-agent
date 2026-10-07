"""Shared admission rules for dashboard Files and file-backed artifacts."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

logger = logging.getLogger(__name__)
MAX_NAME_BYTES = 255
CONTROL_CHARS = frozenset(chr(code) for code in (*range(0x20), 0x7F))


def control_character_in(text: str) -> str:
    for char in text:
        if char in CONTROL_CHARS:
            return f"U+{ord(char):04X}"
    return ""


def _is_system_root(path: str) -> bool:
    from gideon.security.security import _SYSTEM_SUBTREES

    return path == "/" or any(
        path == root or path.startswith(root + os.sep) for root in _SYSTEM_SUBTREES
    )


def dashboard_roots() -> list[tuple[str, str]]:
    """Return the writable roots surfaced by Files, canonicalized and de-duplicated."""
    from gideon.core.config.loader import config_dir, outbox_dir, workspace_root

    candidates: list[tuple[str, str]] = []

    def add(label: str, path_factory) -> None:
        try:
            candidates.append((label, os.path.realpath(str(path_factory()))))
        except Exception:
            return

    add("Workspace", workspace_root)
    add("Outbox", outbox_dir)
    add("Uploads", lambda: os.path.join(config_dir(), "uploads"))

    def add_workspace(label: str, value: str) -> None:
        resolved = os.path.realpath(os.path.expanduser(value))
        if _is_system_root(resolved):
            logger.warning(
                "dashboard: refusing system-root workspace %r as a browsable root",
                resolved,
            )
            return
        candidates.append((label, resolved))

    try:
        from gideon.automation.loop import store as loop_store

        for loop in loop_store.list_all():
            value = (loop.workspace_dir or "").strip()
            if value:
                add_workspace(f"Loop: {loop.name[:24]}", value)
    except Exception:
        pass

    try:
        from gideon.cognition.projects import _store as project_store

        for project in project_store().list_projects():
            value = (project.workspace_dir or "").strip()
            if value:
                add_workspace(f"Project: {project.name[:24]}", value)
    except Exception:
        pass

    try:
        home = os.path.realpath(str(config_dir()))
    except Exception:
        home = ""
    roots: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, root in candidates:
        if not root or root in seen:
            continue
        if home and (root == home or home.startswith(root.rstrip(os.sep) + os.sep)):
            continue
        seen.add(root)
        roots.append((label, root))
    return roots


def within(canonical: str, roots: Iterable[str]) -> bool:
    """Check a canonical path against roots without allowing a broad root into home."""
    from gideon.core.config.loader import config_dir

    home = os.path.realpath(str(config_dir()))
    in_home = canonical == home or canonical.startswith(home + os.sep)
    for root in roots:
        if not root or not (canonical == root or canonical.startswith(root + os.sep)):
            continue
        if in_home and not root.startswith(home + os.sep):
            continue
        return True
    return False


def admit(raw: str, roots: Iterable[str]) -> str | None:
    """Canonicalize and admit a path under roots, rejecting sensitive aliases."""
    if not isinstance(raw, str) or control_character_in(raw):
        return None
    from gideon.engine.hooks import validate_file_path
    from gideon.security.security import (
        HOME_SECRET_FILE_BASENAMES,
        OWN_SECRET_BASENAMES,
    )

    canonical = validate_file_path(raw)
    if canonical is None or not within(canonical, roots):
        return None
    basename = os.path.basename(canonical)
    if len(basename.encode("utf-8")) > MAX_NAME_BYTES:
        return None
    blocked = set(OWN_SECRET_BASENAMES) | set(HOME_SECRET_FILE_BASENAMES)
    folded = basename.casefold()
    if folded in {name.casefold() for name in blocked} or folded.endswith(
        (".key", ".pem", ".secret")
    ):
        return None
    try:
        target = os.stat(canonical)
    except OSError:
        return canonical
    for name in blocked:
        try:
            candidate = os.stat(os.path.join(os.path.dirname(canonical), name))
        except OSError:
            continue
        if (candidate.st_dev, candidate.st_ino) == (target.st_dev, target.st_ino):
            return None
    return canonical
