"""Live transport catalog populated by native boot and enabled channel apps."""

from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.integrations.channel_transports.base import ChannelTransportProvider

_transports: "dict[str, ChannelTransportProvider]" = {}
_catalog_lock = RLock()


def register_transport(provider: "ChannelTransportProvider") -> None:
    with _catalog_lock:
        _transports.update({provider.name: provider})


def unregister_transport(name: str) -> None:
    with _catalog_lock:
        if name in _transports:
            del _transports[name]


def get_transport(name: str) -> "ChannelTransportProvider | None":
    with _catalog_lock:
        return _transports.get(name)


def list_transports() -> list[str]:
    with _catalog_lock:
        return [*_transports]


def register_default_transports() -> None:
    from gideon.integrations.channel_transports.webui import create_provider

    register_transport(create_provider())
