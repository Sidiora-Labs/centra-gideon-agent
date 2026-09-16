"""Keep ordered snapshots of enabled sync transports."""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.integrations.sync_transports.base import SyncTransportProvider

_transports: dict[str, SyncTransportProvider] = {}
_catalog_lock = RLock()


def register_transport(provider: SyncTransportProvider) -> None:
    global _transports
    with _catalog_lock:
        _transports = {**_transports, provider.name: provider}


def unregister_transport(name: str) -> None:
    global _transports
    with _catalog_lock:
        _transports = {
            key: transport for key, transport in _transports.items() if key != name
        }


def get_transport(name: str) -> SyncTransportProvider | None:
    with _catalog_lock:
        return _transports.get(name)


def list_transports() -> list[str]:
    with _catalog_lock:
        return [*_transports]
