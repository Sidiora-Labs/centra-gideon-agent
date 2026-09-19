"""Dashboard session delivery presented through the channel transport contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from gideon.integrations.channel_transports.base import (
    ChannelCapabilities,
    ChannelTransportProvider,
    OutboundMessage,
)

if TYPE_CHECKING:
    from gideon.interfaces.dashboard.state import ConsoleState


@dataclass
class _DashboardBinding:
    state: ConsoleState | None = None
    connect_requested: bool = False

    def publish(self, message: OutboundMessage) -> bool:
        state = self.state
        if state is None:
            return False
        session = state._sessions.get(message.channel_id)
        if session is None:
            return False
        session.append("assistant", message.text, "msg msg-assistant")
        state.push_sessions_update()
        return True


class WebUITransport(ChannelTransportProvider):
    def __init__(self) -> None:
        self._binding = _DashboardBinding()

    def capabilities(self) -> ChannelCapabilities:
        flags = {
            name: True
            for name in (
                "inbound",
                "attachments",
                "edits",
                "rich_text",
                "typing_indicator",
            )
        }
        return ChannelCapabilities(**flags)

    @property
    def name(self) -> str:
        return "webui"

    @property
    def display_name(self) -> str:
        return "Web UI"

    def bind_state(self, state: ConsoleState | None) -> None:
        self._binding.state = state

    async def connect(self) -> bool:
        self._binding.connect_requested = True
        return self._binding.connect_requested

    async def disconnect(self) -> None:
        self._binding.connect_requested = False

    async def send(self, message: OutboundMessage) -> bool:
        return self._binding.publish(message)

    @property
    def connected(self) -> bool:
        return self._binding.state is not None

    async def health(self) -> dict[str, Any]:
        values = (
            ("ready", "in-app websocket")
            if self.connected
            else ("offline", "dashboard not started")
        )
        return dict(zip(("state", "detail"), values))


def create_provider(config: dict[str, Any] | None = None) -> WebUITransport:
    return WebUITransport()
