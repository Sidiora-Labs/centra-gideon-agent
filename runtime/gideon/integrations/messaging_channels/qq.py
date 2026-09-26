"""QQ bot gateway for direct and mentioned group messages."""

from __future__ import annotations

import asyncio
from typing import Any

from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport


class QQTransport(MessagingTransport):
    name = "qq"
    display_name = "QQ"
    required = ("app_id", "secret")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.client: Any = None
        self.chat_types: dict[str, str] = {}
        self.sequence = 1

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    async def _open(self) -> None:
        import botpy

        transport = self

        class Bot(botpy.Client):
            async def on_ready(self) -> None:
                transport._connected = True
                transport._detail = "Connected"

            async def on_c2c_message_create(self, message: Any) -> None:
                await transport._message(message, "c2c")

            async def on_group_at_message_create(self, message: Any) -> None:
                await transport._message(message, "group")

            async def on_direct_message_create(self, message: Any) -> None:
                await transport._message(message, "guild_dm")

        self.client = Bot(intents=botpy.Intents(public_messages=True, direct_message=True), ext_handlers=False)
        self._detail = "Connecting to QQ"

    async def _message(self, message: Any, kind: str) -> None:
        if kind == "group":
            chat = str(getattr(message, "group_openid", "") or "")
            sender = str(getattr(message.author, "member_openid", "") or "")
        else:
            sender = str(
                getattr(message.author, "id", None)
                or getattr(message.author, "user_openid", None)
                or ""
            )
            chat = str(getattr(message, "guild_id", "") or sender) if kind == "guild_dm" else sender
        self.chat_types[chat] = kind
        await self._inbound(
            chat,
            sender,
            str(getattr(message, "content", "") or "").strip(),
            message_id=str(getattr(message, "id", "") or ""),
            is_dm=kind != "group",
            metadata={"sender_name": sender},
        )

    async def _receive(self) -> None:
        while True:
            try:
                await self.client.start(appid=self.config["app_id"], secret=self.config["secret"])
            except asyncio.CancelledError:
                raise
            except Exception:
                self._connected = False
                self._detail = "QQ gateway disconnected; reconnecting"
                await asyncio.sleep(5)

    async def _close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def send(self, message: OutboundMessage) -> bool:
        if not self._connected or self.client is None:
            raise ConnectionError("QQ is disconnected")
        self.sequence += 1
        kind = self.chat_types.get(message.channel_id, "c2c")
        if kind == "group":
            await self.client.api.post_group_message(
                group_openid=message.channel_id,
                msg_type=2,
                markdown={"content": message.text},
                msg_id=None,
                msg_seq=self.sequence,
            )
        elif kind == "guild_dm":
            await self.client.api.post_dms(
                guild_id=message.channel_id,
                content=message.text,
                msg_id=None,
            )
        else:
            await self.client.api.post_c2c_message(
                openid=message.channel_id,
                msg_type=2,
                markdown={"content": message.text},
                msg_id=None,
                msg_seq=self.sequence,
            )
        return True


def create_provider(config: dict[str, Any] | None = None) -> QQTransport:
    return QQTransport(config)
