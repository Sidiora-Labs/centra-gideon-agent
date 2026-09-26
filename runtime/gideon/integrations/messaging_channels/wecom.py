"""WeCom AI bot long connection and reply stream."""

from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict
from typing import Any

from gideon.integrations.channel_transports.base import OutboundMessage
from gideon.integrations.channel_transports.base import ChannelCapabilities
from gideon.integrations.messaging_channels.base import MessagingTransport


class WeComTransport(MessagingTransport):
    name = "wecom"
    display_name = "WeCom"
    required = ("bot_id", "secret")

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.client: Any = None
        self.frames: OrderedDict[str, Any] = OrderedDict()
        self.seen: OrderedDict[str, None] = OrderedDict()

    async def _open(self) -> None:
        from wecom_aibot_sdk import WSClient

        self.client = WSClient(
            {
                "bot_id": self.config["bot_id"],
                "secret": self.config["secret"],
                "reconnect_interval": 1000,
                "max_reconnect_attempts": -1,
                "heartbeat_interval": 30000,
            }
        )
        for kind in ("text", "image", "voice", "file", "mixed"):
            self.client.on(f"message.{kind}", self._handler(kind))
        self.client.on("authenticated", self._authenticated)
        self.client.on("disconnected", self._disconnected)
        self._detail = "Connecting to WeCom"

    async def _authenticated(self, frame: Any) -> None:
        self._connected = True
        self._detail = "Connected"

    async def _disconnected(self, frame: Any) -> None:
        self._connected = False
        self._detail = "WeCom disconnected; reconnecting"

    def _handler(self, kind: str):
        async def receive(frame: Any) -> None:
            body = getattr(frame, "body", None)
            if not isinstance(body, dict):
                return
            sender = body.get("from", {})
            sender_id = str(sender.get("userid") or "") if isinstance(sender, dict) else ""
            chat = str(body.get("chatid") or sender_id)
            message_id = str(body.get("msgid") or "")
            if not chat or not sender_id or not message_id or message_id in self.seen:
                return
            self.seen[message_id] = None
            if len(self.seen) > 1000:
                self.seen.popitem(last=False)
            text = ""
            if kind == "text":
                text = str((body.get("text") or {}).get("content") or "")
            elif kind == "voice":
                text = str((body.get("voice") or {}).get("content") or "")
            elif kind == "mixed":
                text = "\n".join(
                    str((item.get("text") or {}).get("content") or "")
                    for item in (body.get("mixed") or {}).get("item", [])
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            else:
                text = f"[{kind} received in WeCom]"
            self.frames[chat] = frame
            self.frames.move_to_end(chat)
            if len(self.frames) > 1000:
                self.frames.popitem(last=False)
            await self._inbound(
                chat,
                sender_id,
                text,
                message_id=message_id,
                is_dm=body.get("chattype", "single") == "single",
                metadata={"sender_name": sender_id, "message_type": kind},
            )

        return receive

    async def _receive(self) -> None:
        await self.client.connect_async()
        while True:
            await asyncio.sleep(1)

    async def _close(self) -> None:
        if self.client is not None:
            closing = self.client.disconnect()
            if inspect.isawaitable(closing):
                await closing
            self.client = None
        self.frames.clear()

    async def send(self, message: OutboundMessage) -> bool:
        if self.client is None or not self._connected:
            raise ConnectionError("WeCom is disconnected")
        frame = self.frames.get(message.channel_id)
        if frame is None:
            raise LookupError("WeCom requires an inbound conversation before replying")
        from wecom_aibot_sdk import generate_req_id

        await self.client.reply_stream(frame, generate_req_id("stream"), message.text, finish=True)
        return True


def create_provider(config: dict[str, Any] | None = None) -> WeComTransport:
    return WeComTransport(config)
