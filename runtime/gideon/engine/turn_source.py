"""Display provenance carried by transcript rows; never an authority credential."""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

SOURCE_FIELDS = ("source_thread", "source_user")
DASHBOARD_SOURCE = MappingProxyType({field: "dashboard" for field in SOURCE_FIELDS})


def source_of(row: object) -> dict[str, str]:
    if not isinstance(row, Mapping):
        return {}
    return {field: value for field in (*SOURCE_FIELDS, "source_event_id") if isinstance(value := row.get(field), str) and value}


def arrived_on(thread: str | None, sender: str | None) -> dict[str, str]:
    return source_of(dict(zip(SOURCE_FIELDS, (thread, sender))))


def shared_source(rows: Iterable[Mapping[str, object]]) -> dict[str, str]:
    sources = [source_of(row) for row in rows]
    return sources[0] if sources and all(source == sources[0] for source in sources) else {}
