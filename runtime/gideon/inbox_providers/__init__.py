from gideon.inbox_providers.base import MessageSourceProvider
from gideon.provider_registry import discover_providers

_cache: dict[str, type] | None = None


def get_message_providers() -> dict[str, type]:
    global _cache
    if _cache is None:
        _cache = discover_providers(
            "gideon.message_source_providers",
            MessageSourceProvider,  # type: ignore[type-abstract]
        )
    return _cache


def get_default_provider(name: str = "native") -> "MessageSourceProvider":
    """Resolve and instantiate a message-source provider by name.

    Falls back through: requested name → native → filesystem.
    The default is "native" (always-present in-process source); channel-specific
    sources are contributed by their app bundle at enable-time, each registering
    its own ``source_name`` through the ``gideon.message_source_providers``
    entry-point group.
    """
    providers = get_message_providers()
    cls = providers.get(name) or providers.get("native") or providers.get("filesystem")
    if cls is None:
        from gideon.inbox_providers.filesystem_source import FilesystemSourceProvider

        cls = FilesystemSourceProvider
    return cls()
