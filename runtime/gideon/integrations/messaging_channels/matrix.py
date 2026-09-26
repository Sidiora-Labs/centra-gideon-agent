"""Matrix room messaging through the authenticated client session."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from gideon.extensions.providers.settings import ProviderSettings
from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport

logger = logging.getLogger(__name__)


class MatrixTransport(MessagingTransport):
    name = "matrix"
    display_name = "Matrix"
    required = ("homeserver", "user_id", "access_token", "device_id")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.client: Any = None

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, threads=True)

    async def _open(self) -> None:
        from nio import AsyncClient, AsyncClientConfig, RoomMessageText

        store = ProviderSettings.config_path("matrix-channel").parent / "matrix-store"
        store.mkdir(parents=True, exist_ok=True)
        self.client = AsyncClient(
            homeserver=str(self.config["homeserver"]),
            user=str(self.config["user_id"]),
            store_path=str(store),
            config=AsyncClientConfig(
                store_sync_tokens=True,
                encryption_enabled=bool(self.config.get("e2ee_enabled", True)),
            ),
        )
        self.client.user_id = str(self.config["user_id"])
        self.client.access_token = str(self.config["access_token"])
        self.client.device_id = str(self.config["device_id"])
        self.client.load_store()
        self.client.add_event_callback(self._on_message, RoomMessageText)
        identity = await self.client.whoami()
        if getattr(identity, "user_id", None) != self.client.user_id:
            await self.client.close()
            self.client = None
            raise PermissionError("Matrix token does not identify the configured user")
        self._connected = True
        self._detail = "Connected"

    async def _receive(self) -> None:
        while self.client is not None:
            try:
                await self.client.sync_forever(timeout=30000, full_state=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._connected = False
                logger.exception("Matrix sync interrupted")
                await asyncio.sleep(2)

    async def _close(self) -> None:
        if self.client is not None:
            self.client.stop_sync_forever()
            await self.client.close()
            self.client = None

    async def _on_message(self, room: Any, event: Any) -> None:
        if event.sender == self.config["user_id"]:
            return
        is_dm = isinstance(getattr(room, "member_count", None), int) and room.member_count <= 2
        if not is_dm:
            policy = self.config.get("group_policy", "mention")
            if policy == "off":
                return
            source = getattr(event, "source", {}) or {}
            body = source.get("content", {}) if isinstance(source, dict) else {}
            mentions = body.get("m.mentions", {}) if isinstance(body, dict) else {}
            if policy == "mention" and self.config["user_id"] not in mentions.get("user_ids", []):
                return
        source = getattr(event, "source", {}) or {}
        content = source.get("content", {}) if isinstance(source, dict) else {}
        relation = content.get("m.relates_to", {}) if isinstance(content, dict) else {}
        thread = str(relation.get("event_id") or "") if relation.get("rel_type") == "m.thread" else ""
        await self._inbound(
            room.room_id,
            str(event.sender),
            str(event.body or ""),
            message_id=str(event.event_id),
            is_dm=is_dm,
            thread=thread,
            metadata={
                "sender_name": str(event.sender),
                "channel_name": str(getattr(room, "display_name", "") or room.room_id),
            },
        )

    async def send(self, message: OutboundMessage) -> str:
        if self.client is None or not self._connected:
            raise ConnectionError("Matrix is disconnected")
        body = {"msgtype": "m.text", "body": message.text}
        thread = message.thread_id
        prefix = f"matrix:{message.channel_id}:"
        if thread.startswith(prefix):
            thread = thread[len(prefix) :]
        if thread and thread.startswith("$"):
            body["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread,
                "is_falling_back": False,
            }
        response = await self.client.room_send(
            room_id=message.channel_id,
            message_type="m.room.message",
            content=body,
            ignore_unverified_devices=bool(self.config.get("e2ee_enabled", True)),
        )
        if not getattr(response, "event_id", None):
            raise ConnectionError(f"Matrix refused message: {response!s}"[:300])
        return str(response.event_id)


def create_provider(config: dict[str, Any] | None = None) -> MatrixTransport:
    return MatrixTransport(config)
