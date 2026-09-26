"""Management operations over the current transport catalog."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from gideon.integrations.channel_transports import get_transport, list_transports, queued_transport
from gideon.integrations.channel_transports.base import (
    ChannelTransportProvider,
    OutboundMessage,
)

if TYPE_CHECKING:
    from gideon.interfaces.dashboard.state import ConsoleState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TransportProbe:
    transport: ChannelTransportProvider

    async def describe(self) -> dict[str, Any]:
        description = self.transport.info()
        try:
            health = await self.transport.health()
        except Exception as error:
            health = dict(state="error", detail=str(error)[:200])
        description.update(health=health)
        return description

    async def change_connection(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            outcome = await self.transport.connect()
        else:
            await self.transport.disconnect()
            outcome = True
        return dict(ok=outcome, health=await self.transport.health())

    async def check(self) -> dict[str, Any]:
        try:
            result = await self.transport.test()
        except Exception as error:
            result = dict(ok=False, detail=str(error)[:200])
        return result


class ChannelManager:
    def __init__(self, state: ConsoleState | None = None) -> None:
        self._state = state

    def _resolve(self, name: str):
        selected = get_transport(name)
        if selected is not None:
            binder = getattr(selected, "bind_state", None)
            if binder is not None:
                binder(self._state)
        return selected

    async def list(self) -> list[dict[str, Any]]:
        descriptions = []
        for name in list_transports():
            description = await self.get(name)
            if description is not None:
                descriptions.append(description)
        return descriptions

    async def get(self, name: str) -> dict[str, Any] | None:
        selected = self._resolve(name)
        return None if selected is None else await _TransportProbe(selected).describe()

    async def _connection(self, name: str, enabled: bool) -> dict[str, Any]:
        selected = self._resolve(name)
        if selected is None:
            return dict(ok=False, detail="unknown transport")
        return await _TransportProbe(selected).change_connection(enabled)

    async def connect(self, name: str) -> dict[str, Any]:
        return await self._connection(name, True)

    async def disconnect(self, name: str) -> dict[str, Any]:
        return await self._connection(name, False)

    async def test(self, name: str) -> dict[str, Any]:
        selected = self._resolve(name)
        if selected is None:
            return dict(ok=False, detail="unknown transport")
        return await _TransportProbe(selected).check()

    async def send(self, name: str, message: OutboundMessage) -> bool:
        selected = self._resolve(name)
        sender = queued_transport(name) if selected is not None else None
        if sender is None:
            return False
        try:
            return bool(await sender.send(message))
        except Exception:
            logger.exception("channel transport send failed: %s", name)
            return False
