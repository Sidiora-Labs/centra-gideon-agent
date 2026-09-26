"""Durable coverage of readable pages fetched by a research loop."""

from __future__ import annotations

import json
import threading
from urllib.parse import urldefrag, urlsplit

from gideon.automation.loop import files as loop_files
from gideon.core.atomic_write import atomic_write

_LOCK = threading.Lock()
_NAME = "research_sources.json"
_MAX_SOURCES = 512
_MIN_READABLE_CHARS = 200


def _path(loop_id: str):
    directory = loop_files.safe_loop_dir(loop_id)
    return directory / _NAME if directory is not None else None


def _read(loop_id: str) -> list[dict[str, str]]:
    path = _path(loop_id)
    if path is None or not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in value if isinstance(entry, dict)
            and isinstance(entry.get("url"), str)
            and isinstance(entry.get("domain"), str)] if isinstance(value, list) else []


def record(session_key: str, url: str, readable_chars: int) -> None:
    if readable_chars < _MIN_READABLE_CHARS or not session_key.startswith("loop-"):
        return
    from gideon.automation.loop import manager, store

    loop_id = manager.loop_id_from_session_key(session_key)
    loop = store.get(loop_id) if loop_id else None
    if loop is None or loop.kind != "research":
        return
    clean, _ = urldefrag(url)
    parsed = urlsplit(clean)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return
    source = {"url": clean.rstrip("/"), "domain": parsed.hostname.lower()}
    path = _path(loop_id)
    if path is None:
        return
    with _LOCK:
        sources = _read(loop_id)
        if source["url"] in {entry.get("url") for entry in sources}:
            return
        if len(sources) >= _MAX_SOURCES:
            return
        atomic_write(path, json.dumps([*sources, source], ensure_ascii=False))


def coverage(loop_id: str) -> dict[str, int]:
    sources = _read(loop_id)
    return {"pages": len(sources), "domains": len({s.get("domain") for s in sources})}


def unmet_reason(loop_id: str, kind_config: dict | None) -> str | None:
    cfg = kind_config or {}
    try:
        min_pages = max(0, int(cfg.get("evidence_min_pages") or 0))
        min_domains = max(0, int(cfg.get("evidence_min_domains") or 0))
    except (TypeError, ValueError):
        return None
    if not min_pages and not min_domains:
        return None
    have = coverage(loop_id)
    if have["pages"] >= min_pages and have["domains"] >= min_domains:
        return None
    return ("Research readable-source coverage unmet: "
            f"{have['pages']}/{min_pages} distinct pages and "
            f"{have['domains']}/{min_domains} sites.")
