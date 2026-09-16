"""Directory-backed definition discovery and document admission."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def definition_names(root: Path, filename: str) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / filename).is_file()
    )


def definition_window(names: list[str], read: Callable, limit: int, offset: int):
    selected = (read(name) for name in names[offset : offset + max(1, limit)])
    return [definition for definition in selected if definition is not None], len(names)


def read_definition(
    path: Path, name: str, logger, *, bundled: bool = False
) -> dict[str, Any] | None:
    if not bundled and not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(
            "%s %s: unreadable JSON",
            "bundled template" if bundled else "workflow def",
            name,
        )
        return None
    if isinstance(document, dict):
        document.setdefault("name", name)
        return document
    return None
