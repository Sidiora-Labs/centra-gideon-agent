"""Packaged shared prompt blocks with depth-first immutable expansion."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SHARED_PKG = "gideon.automation.workflows.bundled.shared"

BLOCK_REF = re.compile(r"\{\{\s*block:([a-z0-9][a-z0-9-]*)\s*\}\}")


class BlockError(ValueError):
    """A block reference that cannot be resolved. ValueError so the save path's existing
    "unusable spec" channel reports it without a new error taxonomy."""


def shared_root() -> Path:
    """On-disk path of the shared block directory, resolved the same way as bundled templates."""
    return Path(str(resources.files(_SHARED_PKG)))


def block_names() -> list[str]:
    root = shared_root()
    entries = root.glob("*.md") if root.is_dir() else ()
    return sorted(entry.stem for entry in entries)


@lru_cache(maxsize=32)
def _read_cached(name: str, mtime_ns: int) -> str:
    path = shared_root() / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("shared block %s: unreadable", name)
        return ""


def read_block(name: str) -> str:
    """The text of one block, or "" if it does not exist."""
    path = shared_root() / f"{name}.md"
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return ""
    return _read_cached(name, mtime_ns)


def refs_in(value: Any) -> set[str]:
    names: set[str] = set()
    pending = [iter((value,))]
    while pending:
        try:
            item = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if isinstance(item, str):
            names.update(match.group(1) for match in BLOCK_REF.finditer(item))
        elif isinstance(item, (dict, list)):
            pending.append(iter(item.values() if isinstance(item, dict) else item))
    return names


def resolve_text(text: str) -> str:
    parts: list[str] = []
    end = 0
    for match in BLOCK_REF.finditer(text):
        name = match.group(1)
        body = read_block(name)
        if not body:
            raise BlockError(
                f"unknown shared block {name!r} — available: {', '.join(block_names()) or 'none'}"
            )
        parts.extend((text[end : match.start()], body))
        end = match.end()
    parts.append(text[end:])
    return "".join(parts)


def resolve(value: Any) -> Any:
    result: list[Any] = [None]
    frames = [(result, 0, value)]
    while frames:
        parent, key, item = frames.pop()
        if isinstance(item, str):
            replacement = resolve_text(item) if "{{block:" in item else item
        elif isinstance(item, dict):
            replacement = {}
            frames.extend((replacement, k, v) for k, v in reversed(list(item.items())))
        elif isinstance(item, list):
            replacement = [None] * len(item)
            frames.extend(
                (replacement, index, item[index])
                for index in range(len(item) - 1, -1, -1)
            )
        else:
            replacement = item
        parent[key] = replacement
    return result[0]


def resolve_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve every block reference in a whole spec."""
    out = resolve(spec)
    return out if isinstance(out, dict) else spec


def has_refs(spec: Any) -> bool:
    """True when a spec still contains an unresolved block reference."""
    return bool(refs_in(spec))
