"""Live app-built inbox sources, keyed by the instance's declared source name."""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.integrations.inbox_providers.base import MessageSourceProvider

_sources: dict[str, MessageSourceProvider] = {}
_sources_lock = RLock()


def register_source(provider: MessageSourceProvider) -> str:
    key = str(provider.source_name)
    if key == "":
        raise ValueError(
            "an app-contributed inbox source must expose a non-empty source_name"
        )
    with _sources_lock:
        _sources.update({key: provider})
    return key


def unregister_source(name: str) -> None:
    with _sources_lock:
        if name in _sources:
            del _sources[name]


def get_source(name: str) -> MessageSourceProvider | None:
    with _sources_lock:
        return _sources.get(name)


def list_source_names() -> list[str]:
    with _sources_lock:
        names = list(_sources)
    names.sort()
    return names
