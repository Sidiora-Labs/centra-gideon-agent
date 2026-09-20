"""Live notification delivery providers, keyed by delivery name."""

import logging
from threading import RLock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import NotificationDeliveryProvider

_providers: dict[str, "NotificationDeliveryProvider"] = {}
_lock = RLock()
logger = logging.getLogger(__name__)


def register_provider(provider: "NotificationDeliveryProvider") -> str:
    name = str(provider.delivery_name).strip()
    if not name:
        raise ValueError(
            "a notification provider must expose a non-empty delivery_name"
        )
    with _lock:
        _providers[name] = provider
    return name


def unregister_provider(name: str) -> None:
    with _lock:
        _providers.pop(name, None)


def route(notification: dict[str, Any]) -> str:
    addressee = str(notification.get("addressee") or "").strip()
    with _lock:
        providers = list(_providers.items())
    for name, provider in providers:
        try:
            if provider.can_reach(addressee):
                provider.deliver(dict(notification))
                return name
        except Exception:
            logger.warning("notification provider %s failed", name, exc_info=True)
    return ""
