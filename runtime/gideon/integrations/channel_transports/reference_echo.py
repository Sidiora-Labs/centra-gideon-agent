"""Local reference transport with outbound echo and guarded inbound delivery."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from gideon.integrations.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)


@dataclass
class _EchoState:
    online: bool = False
    services: Any = None
    mailbox: asyncio.Queue[ChannelMessage] = field(default_factory=asyncio.Queue)
    sent: list[OutboundMessage] = field(default_factory=list)

    def publish(self, channel: str, text: str, sender: str, thread: str = "") -> None:
        record = ChannelMessage(
            channel, text, sender=sender, thread_id=thread, ts=time.time()
        )
        self.mailbox.put_nowait(record)


@dataclass
class TrustDecision:
    allowed: bool
    reason: str = ""
    paired: bool = False
    notified: bool = False
    delivered_text: str = ""


class ReferenceEchoTransport(ChannelTransportProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._runtime = _EchoState()
        self.sent = self._runtime.sent

    @property
    def name(self) -> str:
        return "reference-echo"

    @property
    def display_name(self) -> str:
        return "Reference (Echo)"

    def capabilities(self) -> ChannelCapabilities:
        flags = dict(inbound=True, threads=True, rich_text=True, max_text_len=4000)
        return ChannelCapabilities(**flags)

    async def connect(self) -> bool:
        self._runtime.online = True
        return self.connected

    async def disconnect(self) -> None:
        self._runtime.online = False

    @property
    def connected(self) -> bool:
        return self._runtime.online

    async def send(self, message: OutboundMessage) -> bool:
        if self.connected:
            self.sent.append(message)
            self._runtime.publish(
                message.channel_id,
                "echo: " + message.text,
                self.name,
                message.thread_id,
            )
            return True
        return False

    async def receive(self) -> AsyncIterator[ChannelMessage]:
        while self.connected:
            try:
                async with asyncio.timeout(1.0):
                    record = await self._runtime.mailbox.get()
            except TimeoutError:
                continue
            yield record

    async def start_inbound(self, services: Any) -> None:
        self._runtime.services = services

    async def stop_inbound(self) -> None:
        self._runtime.services = None

    async def handle_inbound(
        self, msg: ChannelMessage, *, is_dm: bool = True
    ) -> TrustDecision:
        services = self._runtime.services
        if services is None:
            return TrustDecision(False, reason="no_services")
        verdict = await services.deliver_channel_inbound(self.name, msg, is_dm=is_dm)
        result = TrustDecision(
            allowed=verdict.allowed,
            reason=verdict.reason,
            paired=bool(verdict.meta.get("paired")),
            notified=verdict.fired_notification,
        )
        if verdict.allowed:
            result.delivered_text = verdict.fenced_text or msg.text
        if verdict.canned_reply:
            reply = OutboundMessage(msg.channel_id, verdict.canned_reply)
            await self.send(reply)
        return result

    async def _simulate_inbound(self, text: str, channel_id: str = "ref") -> None:
        self._runtime.publish(channel_id, text, "user")
