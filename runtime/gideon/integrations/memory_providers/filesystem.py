"""Adapt a markdown journal to the memory persistence interface."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING

from gideon.integrations.memory_providers.base import MemoryProvider

if TYPE_CHECKING:
    from gideon.cognition.memory import MemoryJournal
    from gideon.cognition.memory_record import MemoryCapabilities, MemoryRecord

_PREFERENCE_KINDS = frozenset(("preference", "semantic", "note"))
_DOCUMENTS = (
    ("preferences", "preference", "read_preferences"),
    ("projects", "note", "read_projects"),
)


def _journal_entries(records: list[MemoryRecord]) -> Iterator[tuple[str, str]]:
    for record in records:
        content = record.text
        if not content:
            content = record.value
            if not isinstance(content, str):
                content = json.dumps(content)
        if content:
            operation = (
                "add_preference"
                if record.kind in _PREFERENCE_KINDS
                else "append_history"
            )
            yield operation, content


def _scored_match(hit: dict) -> dict:
    fields = {"id": "path", "text": "snippet"}
    match = {name: hit.get(source, "") for name, source in fields.items()}
    match.update(score=-float(hit.get("rank", 0.0)), source="fts")
    return match


class FilesystemMemoryProvider(MemoryProvider):
    def __init__(self, store: MemoryJournal) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "filesystem"

    def init(self) -> None:
        self._store.init()

    def capabilities(self) -> MemoryCapabilities:
        from gideon.cognition.memory_record import MemoryCapabilities

        supported = dict.fromkeys(("vector", "transactional_batch", "event_log"), False)
        return MemoryCapabilities(**supported, full_text_search=True)

    def put(self, records: list[MemoryRecord]) -> None:
        for operation, content in _journal_entries(records):
            getattr(self._store, operation)(content)

    def get(self, record_id: str) -> MemoryRecord | None:
        return None

    def delete(self, record_id: str, *, source: str = "user_explicit") -> bool:
        return False

    def query(
        self,
        *,
        kinds: set[str] | None = None,
        scope: str | None = None,
        scope_ref: str | None = None,
        include_deleted: bool = False,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        from gideon.cognition.memory_record import MemoryKind, MemoryRecord

        records = []
        for record_id, kind, reader in _DOCUMENTS:
            content = getattr(self._store, reader)().strip()
            if content and (kinds is None or kind in kinds):
                records.append(
                    MemoryRecord(id=record_id, kind=MemoryKind(kind), text=content)
                )
        return records[slice(None, limit)]

    def vector_query(
        self,
        *,
        text: str = "",
        embedding: list[float] | None = None,
        k: int = 8,
        kinds: set[str] | None = None,
    ) -> list[dict]:
        matches = self._store.search(text, limit=k) if text else ()
        return list(map(_scored_match, matches))

    def embed(self, text: str) -> list[float] | None:
        return None

    def append_event(self, **_kw) -> int:
        return 0

    def read_events(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        return []
