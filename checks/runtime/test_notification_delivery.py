from typing import Any

from gideon.extensions.apps.manifest import PROVIDER_TYPES
from gideon.extensions.providers.registry import (
    NotificationTypeHandler,
    get_provider_registry,
)
from gideon.integrations.notification_providers.base import NotificationDeliveryProvider
from gideon.integrations.notification_providers.registry import (
    register_provider,
    unregister_provider,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.sdk.notification import NotificationDeliveryProvider as SDKProvider


class Delivery(NotificationDeliveryProvider):
    delivery_name = "team"

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def can_reach(self, addressee: str) -> bool:
        return addressee == "alice"

    def deliver(self, notification: dict[str, Any]) -> None:
        self.notes.append(notification)


def test_foreign_note_is_listed_inert_unless_provider_routes(
    monkeypatch, tmp_path
) -> None:
    import gideon.interfaces.dashboard.state as state_module

    persisted: list[dict[str, Any]] = []
    monkeypatch.setattr(state_module, "_persist_notification", persisted.append)
    monkeypatch.setattr(
        "gideon.integrations.inbox.owner_username", lambda: "local-owner"
    )
    state = ConsoleState.__new__(ConsoleState)
    state._notification_log = []

    state.notify("info", "foreign", "body", meta={"addressee": "alice"})
    assert state._notification_log[-1]["withheld_reason"] == "foreign_addressee"
    assert state._notification_log[-1]["routed_to"] == ""

    delivery = Delivery()
    register_provider(delivery)
    try:
        state.notify("info", "routed", "body", meta={"addressee": "alice"})
    finally:
        unregister_provider(delivery.delivery_name)
    assert state._notification_log[-1]["routed_to"] == "team"
    assert delivery.notes[-1]["title"] == "routed"
    assert persisted == state._notification_log
    assert SDKProvider is NotificationDeliveryProvider
    assert "notification" in PROVIDER_TYPES
    assert isinstance(
        get_provider_registry()._type_handlers["notification"], NotificationTypeHandler
    )
