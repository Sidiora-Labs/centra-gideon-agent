"""Discover registered trigger stores without allowing one failed reader to hide local rows."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
_STORES: dict[str, Any] = {}


class StoreDirectory:
    def __init__(self, entries: dict[str, Any]):
        self.entries = entries

    def register(self, name: str, store: Any) -> None:
        if not name:
            raise ValueError(
                "a trigger store must be registered under a non-empty provider name"
            )
        self.entries.update({name: store})

    def remove(self, name: str) -> bool:
        previous = self.entries.pop(name, None)
        return previous is not None

    def read(self, skip) -> list[tuple[str, Any, list[Any]]]:
        ignored = set(map(str, skip))
        result = []
        for name, store in self.entries.copy().items():
            if name in ignored:
                continue
            rows = self.rows(name, store)
            if rows is not None:
                result.append((name, store, rows))
        return result

    @staticmethod
    def rows(name: str, store: Any) -> list[Any] | None:
        try:
            rows = store.load()
        except Exception:
            logger.warning(
                "trigger provider %r could not be read; skipping its rows", name
            )
            return None
        if isinstance(rows, list):
            return rows
        logger.warning(
            "trigger provider %r returned a non-list from load(); skipping", name
        )
        return None


def register_trigger_store(name: str, store: Any) -> None:
    StoreDirectory(_STORES).register(name, store)


def unregister_trigger_store(name: str) -> bool:
    return StoreDirectory(_STORES).remove(name)


def registered_stores() -> dict[str, Any]:
    return _STORES.copy()


def provider_rows_by_store(*, skip: Any = ()) -> list[tuple[str, Any, list[Any]]]:
    return StoreDirectory(_STORES).read(skip)


def provider_rows() -> list[Any]:
    return [row for _, _, rows in provider_rows_by_store() for row in rows]
