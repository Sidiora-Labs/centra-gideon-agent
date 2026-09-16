"""Publish memory provider snapshots and initialize the native store on demand."""

from threading import RLock
from typing import Any

_providers: dict[str, Any] = {}
_catalog_lock = RLock()


def register_provider(name: str, provider: Any) -> None:
    global _providers
    with _catalog_lock:
        _providers = {**_providers, name: provider}


def unregister_provider(name: str) -> None:
    global _providers
    with _catalog_lock:
        _providers = {key: value for key, value in _providers.items() if key != name}


def get_provider(name: str) -> Any:
    with _catalog_lock:
        return _providers.get(name)


def list_providers() -> list[str]:
    with _catalog_lock:
        return [*_providers]


def _initialize_native() -> Any:
    from gideon.cognition.vector_memory import SemanticArchive

    instance = SemanticArchive()
    instance.init()
    return instance


def get_default_provider() -> Any:
    with _catalog_lock:
        if "native" in _providers:
            return _providers["native"]
        initialized = _initialize_native()
        register_provider("native", initialized)
        return initialized


def create_default_provider(config=None):
    return get_default_provider()
