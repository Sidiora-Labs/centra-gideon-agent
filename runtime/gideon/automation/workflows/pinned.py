"Reference-only artifact bookmarks with bounded newest-first ordering."

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_ENTITY = "pinned_artifacts"

MAX_PINS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    from gideon.extensions.providers.entity_routes import _load_entity_settings

    document = _load_entity_settings(_ENTITY)
    entries = document.get("pins") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return []
    normalized = (
        {name: str(entry.get(name) or "") for name in ("slug", "pinned_at", "run_id")}
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("slug") or "")
    )
    return list(normalized)


def _save(pins: list[dict[str, Any]]) -> None:
    from gideon.extensions.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, {"pins": pins[:MAX_PINS]})


def list_pins() -> list[dict[str, Any]]:
    """Every pin, newest-first. References only — the caller resolves each slug."""
    return _load()


def is_pinned(slug: str) -> bool:
    target = (slug or "").strip()
    return bool(target) and target in [entry["slug"] for entry in _load()]


def pin(slug: str, *, run_id: str = "") -> list[dict[str, Any]]:
    target = (slug or "").strip()
    if not target:
        return _load()
    return PinCollection(_load()).place(target, (run_id or "").strip())


def unpin(slug: str) -> list[dict[str, Any]]:
    collection = PinCollection(_load())
    result = collection.without((slug or "").strip())
    _save(result)
    return result


class PinCollection:
    def __init__(self, entries):
        self.entries = entries

    def without(self, slug: str):
        return list(filter(lambda entry: entry["slug"] != slug, self.entries))

    def place(self, slug: str, run_id: str):
        previous = self.without(slug)
        record = {"slug": slug, "pinned_at": _now(), "run_id": run_id}
        result = [record, *previous][:MAX_PINS]
        _save(result)
        return result
