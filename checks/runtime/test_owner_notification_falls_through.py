"""Real channel delivery handles fail over without cross-channel owner reuse."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from gideon.core.config.credentials import owner_id_credential
from gideon.integrations.channel_delivery import reach_owner, register


def _mail_delivery(tmp_path: Path):
    app_root = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/gideonai-mail-desk"
    )
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    from mail_desk_runtime.delivery import MailDeskDelivery, ThreadStore
    from mail_desk_runtime.smtp_client import SmtplibSender

    return MailDeskDelivery(
        SmtplibSender("127.0.0.1", 1, "", "", security="plain"),
        "gideon@example.invalid",
        threads=ThreadStore(lambda: tmp_path / "mail-threads.json"),
    )


def test_unusable_owner_routes_are_tried_in_order_then_reported_once(
    tmp_path, monkeypatch
):
    first, second = "mail-owner-route-a", "mail-owner-route-b"
    monkeypatch.setenv(owner_id_credential(first), "not-an-email-a")
    monkeypatch.setenv(owner_id_credential(second), "not-an-email-b")
    first_delivery = _mail_delivery(tmp_path)
    second_delivery = _mail_delivery(tmp_path)
    register(first_delivery, first)
    register(second_delivery, second)
    inbox_reasons: list[str] = []

    async def send(_provider, _delivery, _destination):
        raise AssertionError("MailDeskDelivery.open_dm must reject invalid addresses")

    async def inbox(reason):
        inbox_reasons.append(reason)

    try:
        result = asyncio.run(
            reach_owner(send, only=(first, second), inbox_fallback=inbox)
        )
    finally:
        register(None, first)
        register(None, second)

    assert result.reason == "inbox fallback"
    assert result.connected_channels == 2
    assert result.attempted_channels == (first, second)
    assert len(result.failures) == 2
    assert "owner destination unavailable" in result.failures[0]
    assert "owner destination unavailable" in result.failures[1]
    assert inbox_reasons == ["; ".join(result.failures)]


def test_no_connected_owner_channel_does_not_create_fallback_noise():
    fallback_calls: list[str] = []

    async def send(_provider, _delivery, _destination):
        raise AssertionError("there is no registered channel")

    async def inbox(reason):
        fallback_calls.append(reason)

    result = asyncio.run(
        reach_owner(
            send, only=("channels-owner-unregistered-test",), inbox_fallback=inbox
        )
    )
    assert not result.delivered
    assert result.connected_channels == 0
    assert result.reason == "no connected channels"
    assert fallback_calls == []
