"""Queue failure and retirement through Gideon's actual Web UI transport."""

from __future__ import annotations

import asyncio

import pytest

from gideon.integrations.channel_transports.base import OutboundMessage
from gideon.integrations.channel_transports.webui import WebUITransport
from gideon.integrations.channel_transports import register_transport, unregister_transport
from gideon.integrations import channel_delivery
from gideon.integrations.messaging_channels.whatsapp import WhatsAppTransport
from gideon.integrations.outbound_queue import QueuedDelivery


def test_unbound_webui_send_retries_and_reports_final_failure() -> None:
    async def exercise() -> None:
        queue = QueuedDelivery("webui", WebUITransport())
        with pytest.raises(ConnectionError, match="no delivery receipt"):
            await queue.send(OutboundMessage("absent-session", "hello"))
        assert queue.exhausted == 1
        queue.retire()
        with pytest.raises(RuntimeError, match="retired"):
            await queue.send(OutboundMessage("absent-session", "again"))

    asyncio.run(exercise())


def test_retired_channel_manager_sender_does_not_dispatch() -> None:
    async def exercise() -> None:
        queue = QueuedDelivery("webui", WebUITransport())
        queue.retire()
        with pytest.raises(RuntimeError, match="retired"):
            await queue.send(OutboundMessage("absent-session", "hello"))
        assert queue.exhausted == 0

    asyncio.run(exercise())


def test_retirement_during_active_retry_reports_failure() -> None:
    async def exercise() -> None:
        queue = QueuedDelivery("webui", WebUITransport())
        sending = asyncio.create_task(
            queue.send(OutboundMessage("absent-session", "hello"))
        )
        for _ in range(100):
            if queue._active is not None:
                break
            await asyncio.sleep(0)
        assert queue._active is not None
        queue.retire()
        with pytest.raises(RuntimeError, match="retired"):
            await sending

    asyncio.run(exercise())


def test_disconnected_channel_does_not_take_owner_notifications() -> None:
    provider = WhatsAppTransport({})
    register_transport(provider)
    channel_delivery.register(provider.delivery, provider.name)
    try:
        assert channel_delivery.owner_reachable() is None
    finally:
        channel_delivery.register(None, provider.name)
        unregister_transport(provider.name)
