"""Active external knowledge vector-store provider."""

from __future__ import annotations

import logging

from gideon.integrations.vector_store_providers.base import VectorStoreProvider

logger = logging.getLogger(__name__)

_provider: VectorStoreProvider | None = None


def register_provider(provider: VectorStoreProvider, *, store=None) -> None:
    global _provider
    _provider = provider
    try:
        if store is None:
            from gideon.cognition.knowledge import get_knowledge_store

            store = get_knowledge_store()
        result = store.reindex_external_vector_store()
        if not result["ok"]:
            logger.warning("External vector-store reindex incomplete: %s", result)
    except Exception:
        logger.warning(
            "External vector-store reindex failed for %s", provider.name, exc_info=True
        )


def unregister_provider(name: str) -> None:
    global _provider
    if _provider is not None and _provider.name == name:
        _provider = None


def get_provider() -> VectorStoreProvider | None:
    return _provider


def provider_for_health() -> VectorStoreProvider | None:
    if _provider is not None:
        return _provider
    from gideon.extensions.providers.loader import discover_installed_extensions
    from gideon.extensions.providers.registry import (
        RegisteredProvider,
        VectorStoreTypeHandler,
    )

    selected = None
    for manifest, enabled in discover_installed_extensions():
        if not enabled:
            continue
        for config in manifest.all_providers():
            if config.type == "vector_store":
                selected = VectorStoreTypeHandler().create(
                    RegisteredProvider(
                        name=manifest.name,
                        manifest=manifest,
                        provider_config=config,
                    )
                )
    return selected
