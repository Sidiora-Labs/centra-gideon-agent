"""Exercise the real registered local transport boundary with credential-shaped content."""

import asyncio

import pytest

from gideon.integrations.channel_transports import (
    get_transport,
    queued_transport,
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.base import OutboundMessage
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport

SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)
MASK = "[REDACTED: credential]"


@pytest.mark.asyncio
async def test_registered_reference_transport_receives_masked_text_and_metadata():
    previous = get_transport("reference-echo")
    provider = ReferenceEchoTransport()
    register_transport(provider)
    try:
        assert await provider.connect()
        queued = queued_transport(provider.name)
        assert queued is not None
        original = OutboundMessage(
            channel_id="room-42",
            text=f"deploy key {SECRET}",
            thread_id="thread-9",
            metadata={"text": f"caption {SECRET}", "channel_id": "room-42"},
        )
        assert await queued.send(original) is True

        [handed] = provider.sent
        assert handed.text == f"deploy key {MASK}"
        assert handed.metadata == {"text": f"caption {MASK}", "channel_id": "room-42"}
        assert handed.channel_id == original.channel_id
        assert handed.thread_id == original.thread_id
        assert original.text == f"deploy key {SECRET}"
        assert SECRET not in repr(handed)

        async with asyncio.timeout(1):
            received = await anext(provider.receive())
        assert received.text == f"echo: deploy key {MASK}"
        assert received.channel_id == "room-42"
        assert received.thread_id == "thread-9"
    finally:
        unregister_transport(provider.name)
        await provider.disconnect()
        if previous is not None:
            register_transport(previous)
