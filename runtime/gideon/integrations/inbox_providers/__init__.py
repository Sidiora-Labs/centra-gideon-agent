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
                "gideon.message_source_providers",
                MessageSourceProvider,  # type: ignore[type-abstract]
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


def source_catalog(
    fallback: MessageSourceProvider | None = None,
) -> list[tuple[MessageSourceProvider, bool]]:
    """Return a fresh list of active source instances and their polling posture."""
    from gideon.integrations.inbox_providers.registry import (
        get_source,
        list_source_names,
    )

    sources: dict[str, MessageSourceProvider] = {}
    for name in list_source_names():
        instance = get_source(name)
        if instance is not None:
            sources[name] = instance
    if fallback is not None:
        sources.setdefault(str(fallback.source_name), fallback)
    if "filesystem" not in sources:
        try:
            sources["filesystem"] = get_default_provider("filesystem")
        except Exception:
            pass

    try:
        from gideon.core.config.loader import AppConfig

        filesystem_enabled = bool(AppConfig.load().inbox.enabled)
    except Exception:
        filesystem_enabled = False
    return [
        (
            source,
            bool(getattr(source, "polling_enabled", True))
            and (name != "filesystem" or filesystem_enabled),
        )
        for name, source in sorted(sources.items())
    ]
