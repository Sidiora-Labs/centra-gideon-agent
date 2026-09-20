"""Active external knowledge vector-store provider."""

from __future__ import annotations

from gideon.integrations.vector_store_providers.base import VectorStoreProvider

_provider: VectorStoreProvider | None = None


def register_provider(provider: VectorStoreProvider) -> None:
    global _provider
    _provider = provider


def unregister_provider(name: str) -> None:
    global _provider
    if _provider is not None and _provider.name == name:
        _provider = None


def get_provider() -> VectorStoreProvider | None:
    return _provider
