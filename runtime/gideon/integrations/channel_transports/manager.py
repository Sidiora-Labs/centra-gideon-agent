"""Management operations over the current transport catalog."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from gideon.integrations.channel_transports import (
    _safe_detail,
    _safe_value,
    channel_health,
    get_transport,
    list_transports,
    queued_transport,
    settled,
)
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
        health = await channel_health(self.transport)
        description.update(health=health)
        return description

    async def change_connection(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            outcome = await self.transport.connect()
        else:
            await self.transport.disconnect()
            outcome = True
        return dict(ok=outcome, health=await channel_health(self.transport))

    async def check(self) -> dict[str, Any]:
        try:
            result = await self.transport.test()
        except Exception as error:
            result = dict(ok=False, detail=_safe_detail(error))
        status = await channel_health(self.transport)
        if result.get("ok") and status.get("state") != "ready":
            probe = str(result.get("detail") or "").rstrip(".")
            detail = str(status.get("detail") or status.get("state") or "")
            result = {
                **result,
                "ok": False,
                "detail": f"{probe}, but {detail}" if probe else detail,
            }
        elif "detail" in result:
            result = {**result, "detail": _safe_detail(result["detail"])}
        return _safe_value(result)


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
        await settled()
        descriptions = []
        for name in list_transports():
            description = await self.get(name)
            if description is not None:
                descriptions.append(description)
        return descriptions

    async def get(self, name: str) -> dict[str, Any] | None:
        await settled()
        selected = self._resolve(name)
        if selected is None:
            return None
        description = await _TransportProbe(selected).describe()
        from gideon.integrations.channel_trust import owner_ref
        description.update(owner_ref(name))
        from gideon.extensions.providers.registry import get_provider_registry

        owner = next(
            (
                ext.name
                for ext in get_provider_registry().list_by_type("channel")
                if ext.enabled and ext.provider_instance is selected
            ),
            None,
        )
        if owner:
            description["app"] = owner
        return description

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
