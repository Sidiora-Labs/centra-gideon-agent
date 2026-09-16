"""Resolve inbox sources from live app instances and discovered provider classes."""

from threading import RLock

from gideon.extensions.provider_registry import discover_providers
from gideon.integrations.inbox_providers.base import MessageSourceProvider

_cache: dict[str, type] | None = None
_discovery_lock = RLock()


def get_message_providers() -> dict[str, type]:
    global _cache
    with _discovery_lock:
        if _cache is None:
            discovered = discover_providers(
                "gideon.message_source_providers", MessageSourceProvider
            )
            _cache = discovered
        return _cache


def get_default_provider(name: str = "native") -> MessageSourceProvider:
    from gideon.integrations.inbox_providers.registry import get_source

    instance = get_source(name)
    if instance is not None:
        return instance
    catalog = get_message_providers()
    for candidate in (name, "native", "filesystem"):
        implementation = catalog.get(candidate)
        if implementation:
            return implementation()
    from gideon.integrations.inbox_providers.filesystem_source import (
        FilesystemSourceProvider,
    )

    return FilesystemSourceProvider()
