"""Common lifecycle and delivery for platform messaging transports."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

from gideon.integrations import channel_delivery
from gideon.integrations.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

logger = logging.getLogger(__name__)


class MessageDelivery:
    allows_empty_receipt = True

    def __init__(self, transport: "MessagingTransport") -> None:
        self.transport = transport

    async def open_dm(self, user_id: str) -> str:
        return user_id

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **kwargs: Any) -> str:
        result = await self.transport.send(OutboundMessage(channel, text, thread_ts))
        if not result:
            raise ConnectionError(f"{self.transport.name} message was not accepted")
        return str(result) if result is not True else ""

    async def deliver_rich(self, channel: str, payload: object, fallback_text: str, *, thread_ts: str = "", **kwargs: Any) -> str:
        return await self.deliver_text(channel, fallback_text, thread_ts)

    async def deliver_cron_result(self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = "") -> str:
        return await self.deliver_text(channel, f"{job_name}\n{text}", thread_ts)

    async def deliver_notification(self, channel: str, title: str, text: str, thread_ts: str = "") -> str:
        return await self.deliver_text(channel, f"{title}\n{text}", thread_ts)

    async def deliver_chat_mirror(self, channel: str, text: str, thread_ts: str = "") -> None:
        await self.deliver_text(channel, text, thread_ts)

    async def deliver_subagent_reply(self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0) -> None:
        await self.deliver_text(channel, text, thread_ts)

    async def resolve_user_name(self, user_id: str) -> str:
        return user_id

    async def resolve_user_profile(self, user_id: str) -> dict[str, Any]:
        return {"id": user_id}

    async def channel_info(self, channel_id: str) -> dict[str, Any]:
        return {"id": channel_id, "name": self.transport._channels.get(channel_id, channel_id)}

    def list_reply_channels(self) -> list[dict[str, str]]:
        return [
            {"id": channel, "name": name}
            for channel, name in self.transport._channels.items()
        ]

    def is_tracked_channel(self, channel_id: str) -> bool:
        from gideon.integrations.channel_trust import is_tracked_channel

        return is_tracked_channel(self.transport.name, channel_id)

    def build_thread_link(self, channel: str, ts: str) -> str:
        return ""

    async def upload_attachment(self, channel: str, file_path: str, **kwargs: Any) -> str:
        raise NotImplementedError(f"{self.transport.name} does not support file upload")

    async def start_stream(self, channel: str, thread_ts: str = "", initial_text: str = "") -> str:
        return ""

    async def append_stream_task(self, channel: str, stream_ts: str, task_id: str, title: str, status: str) -> None:
        return None

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        return None

    async def request_approval(self, event: object, *, source: str, parent_session_key: str = "", sessions: object | None = None, on_prompted: Any = None) -> None:
        return None


class MessagingTransport(ChannelTransportProvider):
    name = ""
    display_name = ""
    required: tuple[str, ...] = ()

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.services: Any = None
        self.delivery = MessageDelivery(self)
        self._task: asyncio.Task[Any] | None = None
        self._connected = False
        self._detail = "Configure the channel credentials in Apps."
        self._channels: OrderedDict[str, str] = OrderedDict()

    @property
    def connected(self) -> bool:
        return self._connected

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    async def connect(self) -> bool:
        if self.services is not None:
            await self.start_inbound(self.services)
        return self.connected

    async def disconnect(self) -> None:
        await self.stop_inbound()

    async def health(self) -> dict[str, Any]:
        return {
            "state": "ready" if self.connected else "offline",
            "detail": "Connected" if self.connected else self._detail,
        }

    async def start_inbound(self, services: Any) -> None:
        self.services = services
        absent = [key for key in self.required if not self.config.get(key)]
        if absent:
            self._detail = "Missing settings: " + ", ".join(absent)
            return
        if self._task is not None and not self._task.done():
            return
        await self._open()
        if hasattr(services, "register_channel_delivery"):
            services.register_channel_delivery(self.delivery, self.name)
        self._task = asyncio.create_task(self._receive())

    async def stop_inbound(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._close()
        self._connected = False
        if channel_delivery.raw_delivery_for(self.name) is self.delivery:
            channel_delivery.register(None, self.name)

    async def _open(self) -> None:
        raise NotImplementedError

    async def _receive(self) -> None:
        raise NotImplementedError

    async def _close(self) -> None:
        raise NotImplementedError

    async def _inbound(self, chat: str, sender: str, text: str, *, message_id: str, is_dm: bool, thread: str = "", metadata: dict[str, Any] | None = None) -> None:
        if not text.strip() or not chat or not sender or not message_id:
            return
        msg = ChannelMessage(
            channel_id=chat,
            text=text,
            sender=sender,
            thread_id=f"{self.name}:{chat}:{thread}" if thread else f"{self.name}:{chat}",
            message_id=message_id,
            ts=time.time(),
            metadata=metadata or {},
        )
        verdict = await self.services.deliver_channel_inbound(self.name, msg, is_dm=is_dm)
        if verdict.allowed:
            self._channels[chat] = str((metadata or {}).get("channel_name") or chat)
            self._channels.move_to_end(chat)
            if len(self._channels) > 200:
                self._channels.popitem(last=False)
        if verdict.canned_reply:
            delivery = channel_delivery.delivery_for(self.name)
            if delivery is not None:
                await delivery.deliver_text(chat, verdict.canned_reply, thread)
