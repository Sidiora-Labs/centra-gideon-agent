"""Bounded filesystem and Git activity probes for optional trigger deferral."""

from __future__ import annotations

import glob as _glob
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_RECENT_SECS = 300.0
_GIT_TIMEOUT_SECS = 3.0


@dataclass(frozen=True)
class TargetPaths:
    root: Path | None

    def resolve(self, value: Any) -> Path | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            candidate = Path(text).expanduser()
            return (
                Path(self.root) / candidate
                if self.root is not None and not candidate.is_absolute()
                else candidate
            )
        except (OSError, ValueError):
            return None

    def expand(self, pattern: str) -> list[Path]:
        try:
            expanded = Path(pattern).expanduser()
            if expanded.is_absolute():
                found = list(map(Path, _glob.glob(str(expanded))))
            elif self.root is not None:
                found = list(Path(self.root).glob(pattern))
            else:
                found = []
        except (OSError, ValueError):
            return []
        if found:
            return found
        direct = self.resolve(pattern)
        try:
            return [direct] if direct is not None and direct.exists() else []
        except OSError:
            return []


@dataclass(frozen=True)
class ActivityProbe:
    specification: dict[str, Any]
    base_dir: Path | None

    def path_changes(self, now: float) -> str:
        declared = self.specification.get("paths")
        if not isinstance(declared, (list, tuple)) or not declared:
            return ""
        try:
            period = float(self.specification.get("recent_secs") or DEFAULT_RECENT_SECS)
        except (TypeError, ValueError):
            period = DEFAULT_RECENT_SECS
        if period <= 0:
            return ""
        for declaration in declared:
            pattern = str(declaration or "").strip()
            if pattern:
                for candidate in _expand(pattern, self.base_dir):
                    if _recently_modified(candidate, now=now, window=period):
                        return f"{candidate} was modified within {int(period)}s; deferred until it settles"
        return ""

    def locks(self) -> str:
        declared = self.specification.get("lockfiles")
        if not isinstance(declared, (list, tuple)) or not declared:
            return ""
        for declaration in declared:
            path = _resolve(declaration, self.base_dir)
            if path is None:
                continue
            try:
                exists = path.exists()
            except OSError:
                continue
            if exists:
                return f"lock file {path} is present; deferred until it clears"
        return ""

    def git_changes(self) -> str:
        root = _resolve(self.specification.get("dirty_git"), self.base_dir)
        if root is None:
            return ""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECS,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode == 0 and result.stdout.strip():
            return f"git worktree {root} has uncommitted changes; deferred until it is clean"
        return ""


def _resolve(target: Any, base_dir: Path | None) -> Path | None:
    return TargetPaths(base_dir).resolve(target)


def _expand(pattern: str, base_dir: Path | None) -> list[Path]:
    return TargetPaths(base_dir).expand(pattern)


def _recently_modified(path: Path, *, now: float, window: float) -> bool:
    try:
        fresh = now - path.stat().st_mtime < window
        if fresh or not path.is_dir():
            return fresh
        for child in path.iterdir():
            try:
                fresh = now - child.stat().st_mtime < window
            except OSError:
                continue
            if fresh:
                return True
    except OSError:
        pass
    return False


def _paths_active(spec: dict[str, Any], *, now: float, base_dir: Path | None) -> str:
    return ActivityProbe(spec, base_dir).path_changes(now)


def _lockfiles_active(spec: dict[str, Any], *, base_dir: Path | None) -> str:
    return ActivityProbe(spec, base_dir).locks()


def _dirty_git_active(spec: dict[str, Any], *, base_dir: Path | None) -> str:
    return ActivityProbe(spec, base_dir).git_changes()


def is_target_active(
    skip_if_active: Any, *, now: float, base_dir: Path | str | None = None
) -> tuple[bool, str]:
    if not isinstance(skip_if_active, dict) or not skip_if_active:
        return False, ""
    root = Path(base_dir) if base_dir else None
    try:
        findings = (
            _paths_active(skip_if_active, now=now, base_dir=root),
            _lockfiles_active(skip_if_active, base_dir=root),
            _dirty_git_active(skip_if_active, base_dir=root),
        )
        reason = next((finding for finding in findings if finding), "")
        return bool(reason), reason
    except Exception:
        logger.debug(
            "skip_if_active probe failed; treating target as not active", exc_info=True
        )
        return False, ""
