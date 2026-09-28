from gideon.automation.triggers.delivery import build_delivery, is_duplicate
from gideon.interfaces.dashboard.state import _channel_notification_text
from gideon.workspace import notification_rules


def test_channel_dm_target_and_named_destination_are_preserved_and_deduplicated():
    assert "channel_dm" in notification_rules.TARGETS
    delivery = build_delivery(
        trigger_id="daily-check", trigger_name="Daily check", ok=True,
        summary="Finished.", destination="channel:reference-echo:room-a",
    )
    kwargs = delivery.to_notify_kwargs()
    assert kwargs["destination"] == "channel:reference-echo:room-a"
    assert is_duplicate(delivery, {delivery.event_id})
    assert not is_duplicate(delivery, set())
    text = _channel_notification_text("Status", "No secret: sk-12345678901234567890123456789012", {})
    assert "Status" in text and "sk-12345678901234567890123456789012" not in text
