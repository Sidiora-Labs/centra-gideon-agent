"""Feishu message channel using the maintained Lark Channel SDK."""

from __future__ import annotations

import asyncio
from typing import Any

from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport


class FeishuTransport(MessagingTransport):
    name = "feishu"
    display_name = "Feishu"
    required = ("app_id", "app_secret")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.channel: Any = None

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, threads=True)

    async def _open(self) -> None:
        from lark_channel import FeishuChannel

        self.channel = FeishuChannel(
            app_id=str(self.config["app_id"]),
            app_secret=str(self.config["app_secret"]),
            encrypt_key=str(self.config.get("encrypt_key") or ""),
            verification_token=str(self.config.get("verification_token") or ""),
        )
        self.channel.on("message", self._on_message)
        self.channel.on("reconnecting", self._on_reconnecting)
        self.channel.on("reconnected", self._on_reconnected)
        if self.config.get("group_policy") == "open":
            self.channel.update_policy(require_mention=False)
        try:
            await self.channel.connect_until_ready(timeout=30)
        except Exception:
            await self.channel.disconnect()
            self.channel = None
            raise
        self._connected = True
        self._detail = "Connected"

    def _on_reconnecting(self) -> None:
        self._connected = False
        self._detail = "Feishu disconnected; reconnecting"

    def _on_reconnected(self) -> None:
        self._connected = True
        self._detail = "Connected"

    async def _on_message(self, message: Any) -> None:
        if getattr(message, "sender_is_bot", False):
            return
        is_dm = str(getattr(message, "chat_type", "")) == "p2p"
        policy = self.config.get("group_policy", "mention")
        if not is_dm and (policy == "off" or (policy == "mention" and not getattr(message, "mentioned_bot", False))):
            return
        chat = str(getattr(message, "chat_id", "") or "")
        sender = str(getattr(message, "sender_id", "") or "")
        conversation = getattr(message, "conversation", None)
        thread = str(getattr(conversation, "thread_id", "") or "")
        await self._inbound(
            chat,
            sender,
            str(getattr(message, "body_text", "") or getattr(message, "content_text", "") or ""),
            message_id=str(getattr(message, "message_id", "") or ""),
            is_dm=is_dm,
            thread=thread,
            metadata={"sender_name": str(getattr(message, "sender_name", "") or sender)},
        )

    async def _receive(self) -> None:
        await asyncio.Future()

    async def _close(self) -> None:
        if self.channel is not None:
            await self.channel.disconnect()
            self.channel = None

    async def send(self, message: OutboundMessage) -> bool:
        if self.channel is None or not self._connected:
            raise ConnectionError("Feishu is disconnected")
        options: dict[str, Any] = {}
        thread = message.thread_id
        prefix = f"feishu:{message.channel_id}:"
        if thread.startswith(prefix):
            thread = thread[len(prefix) :]
        if thread:
            options = {"reply_to": thread, "reply_in_thread": True}
        outcome = await self.channel.send(message.channel_id, {"text": message.text}, options)
        if not outcome.success:
            raise ConnectionError(f"Feishu rejected message: {outcome.error}")
        return True


def create_provider(config: dict[str, Any] | None = None) -> FeishuTransport:
    return FeishuTransport(config)
