"""Version-bound canvas review receipts for visual Design loops."""

from __future__ import annotations

import json
import threading
import time

from gideon.automation.loop import files as loop_files
from gideon.core.atomic_write import atomic_write

_LOCK = threading.Lock()
_NAME = "design_preview.json"


def receipts(loop_id: str) -> dict[str, dict]:
    directory = loop_files.safe_loop_dir(loop_id)
    path = directory / _NAME if directory is not None else None
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {slug: entry for slug, entry in value.items()
            if isinstance(slug, str) and isinstance(entry, dict)
            and type(entry.get("version")) is int} if isinstance(value, dict) else {}


def record(loop_id: str, slug: str, version: int, approved: bool) -> None:
    directory = loop_files.safe_loop_dir(loop_id)
    if directory is None or not slug or type(version) is not int or version < 1:
        return
    with _LOCK:
        current = receipts(loop_id)
        if approved:
            current[slug] = {"version": version, "reviewed_at": time.time()}
        elif current.get(slug, {}).get("version") == version:
            current.pop(slug, None)
        atomic_write(directory / _NAME, json.dumps(current, ensure_ascii=False))


def all_current_reviewed(loop_id: str, artifacts: list) -> bool:
    visual = [art for art in artifacts if getattr(art, "kind", "") == "react"]
    if not visual:
        return False
    current = receipts(loop_id)
    return all(current.get(art.slug, {}).get("version") == art.version for art in visual)
