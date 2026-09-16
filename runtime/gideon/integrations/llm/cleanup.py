"""Contain session-file deletion within its designated directory."""

from pathlib import Path


def _is_safe_path(target: Path, expected_root: Path) -> bool:
    try:
        relative = target.resolve().relative_to(expected_root.resolve())
    except (OSError, ValueError, RuntimeError):
        return False
    return bool(relative.parts)
