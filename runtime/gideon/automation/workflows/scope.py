"""Declared write boundaries, bounded filesystem observations and scope-change reports."""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PRUNE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".turbo",
    }
)

MAX_SNAPSHOT_ENTRIES = 50_000


class ScopeMode:
    """What to do when a write escapes. `WARN` keeps the node's outcome and records the
    violation; `REJECT` flips the node to `scope_violation`."""

    WARN = "warn"
    REJECT = "reject"


@dataclass
class Snapshot:
    """Paths → (mtime_ns, size). Compared by value, so a rewrite that preserves size is
    still caught via mtime, and a same-mtime size change is caught via size."""

    entries: dict[str, tuple[int, int]] = field(default_factory=dict)
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class ScopeReport:
    """The diff verdict. `violations` are the paths that escaped every allowed glob."""

    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    incomplete: bool = False

    @property
    def clean(self) -> bool:
        return not self.violations

    @property
    def changed(self) -> list[str]:
        return sorted({*self.created, *self.modified, *self.deleted})

    def to_dict(self) -> dict[str, Any]:
        data: dict = {
            name: list(getattr(self, name))
            for name in ("created", "modified", "deleted", "violations")
        }
        data["incomplete"] = self.incomplete
        return data


def normalize(path: str | os.PathLike[str]) -> str:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return os.path.normpath(os.path.abspath(str(path)))
    return str(resolved)


def in_scope(path: str, allowed: list[str]) -> bool:
    if not allowed:
        return False
    target = normalize(path)
    return any(_ScopeRule(raw).matches(target) for raw in allowed)


def snapshot(roots: list[str]) -> Snapshot:
    result = Snapshot()
    for root, path, metadata in _observed_files(roots):
        result.entries[path] = metadata
        if len(result.entries) >= MAX_SNAPSHOT_ENTRIES:
            result.truncated = True
            logger.warning(
                "write-scope snapshot truncated at %d entries under %s",
                MAX_SNAPSHOT_ENTRIES,
                root,
            )
            break
    return result


def diff(before: Snapshot, after: Snapshot, allowed: list[str]) -> ScopeReport:
    changes: dict = {"created": [], "modified": [], "deleted": []}
    for path, current in after.entries.items():
        previous = before.entries.get(path)
        category = (
            "created"
            if previous is None
            else "modified" if previous != current else None
        )
        if category is not None:
            changes[category].append(path)
    changes["deleted"].extend(
        path for path in before.entries if path not in after.entries
    )
    report = ScopeReport(
        **{name: sorted(paths) for name, paths in changes.items()},
        incomplete=before.truncated or after.truncated,
    )
    report.violations.extend(
        path for path in report.changed if not in_scope(path, allowed)
    )
    return report


def allowed_write_paths(node_config: dict[str, Any], workspace: str) -> list[str]:
    declared = _declared_paths((node_config or {}).get("allowed_write_paths"))
    return declared + ([workspace] if workspace else [])


def watch_roots(node_config: dict[str, Any], workspace: str) -> list[str]:
    configured = (node_config or {}).get("watch_roots")
    if (isinstance(configured, list) and configured) or (
        isinstance(configured, str) and configured.strip()
    ):
        return _declared_paths(configured)
    roots = list(allowed_write_paths(node_config, workspace))
    if workspace:
        parent = os.path.dirname(normalize(workspace))
        roots.extend([parent] if parent and parent != os.sep else [])
    return roots


def scope_mode(node_config: dict[str, Any], default: str = ScopeMode.WARN) -> str:
    supplied = (
        str((node_config or {}).get("write_scope_mode", "") or "").strip().lower()
    )
    return next(
        (
            choice
            for choice in (supplied, default)
            if choice in (ScopeMode.WARN, ScopeMode.REJECT)
        ),
        ScopeMode.WARN,
    )


def enforces_scope(node_config: dict[str, Any]) -> bool:
    """Whether a node opts into scope checking."""
    cfg = node_config or {}
    return bool(cfg.get("allowed_write_paths") or cfg.get("write_scope_mode"))


def _declared_paths(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(path) for path in value if str(path or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


class _ScopeRule:
    def __init__(self, raw: Any):
        self.pattern = str(raw or "").strip()

    def matches(self, target: str) -> bool:
        if not self.pattern:
            return False
        expanded = os.path.expanduser(self.pattern)
        if any(char in self.pattern for char in "*?["):
            glob = os.path.normpath(os.path.abspath(expanded))
            if fnmatch.fnmatch(target, glob):
                return True
            root = glob.split("*", 1)[0].rstrip(os.sep)
        else:
            root = normalize(expanded)
        return bool(root and (target == root or target.startswith(root + os.sep)))


def _observed_files(roots: list[str]):
    for source in roots:
        root = normalize(os.path.expanduser(str(source or "")))
        if not root or not os.path.isdir(root):
            continue
        for directory, children, files in os.walk(root, followlinks=False):
            children[:] = filter(lambda name: name not in _PRUNE_DIRS, children)
            for name in files:
                path = os.path.join(directory, name)
                try:
                    metadata = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                yield root, path, (metadata.st_mtime_ns, metadata.st_size)
