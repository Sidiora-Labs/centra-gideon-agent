"""Channel receiver ownership follows real transport registration and gateway life."""

from __future__ import annotations

import asyncio

import pytest

from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.integrations import channel_delivery
from gideon.integrations.channel_transports import (
    _safe_detail,
    bind_inbound,
    settled,
    unbind_inbound,
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))


async def _await_owner(provider: ReferenceEchoTransport, gateway: RuntimeCoordinator) -> None:
    for _ in range(100):
        await settled()
        if provider._runtime.services is gateway:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the registered receiver did not bind to the active gateway")


def test_replacement_and_second_gateway_release_old_services_and_delivery() -> None:
    async def exercise() -> None:
        first = RuntimeCoordinator(AppConfig(), no_dashboard=True, no_crons=True)
        second = RuntimeCoordinator(AppConfig(), no_dashboard=True, no_crons=True)
        original = ReferenceEchoTransport()
        replacement = ReferenceEchoTransport()
        await original.connect()
        await replacement.connect()
        register_transport(original)
        try:
            await bind_inbound(first)
            await _await_owner(original, first)

            register_transport(replacement)
            await _await_owner(replacement, first)
            assert original._runtime.services is None

            channel_delivery.register(original, provider=original.name)
            await unbind_inbound()
            assert replacement._runtime.services is None
            assert channel_delivery.delivery_for(replacement.name) is None

            await bind_inbound(second)
            await _await_owner(replacement, second)
            assert replacement._runtime.services is second
        finally:
            await unbind_inbound()
            unregister_transport(replacement.name)
            await original.disconnect()
            await replacement.disconnect()

    asyncio.run(exercise())


def test_channel_status_detail_masks_credentials_and_url_paths() -> None:
    visible = _safe_detail(
        "request failed at https://owner:token@example.test/private/bot?secret=value"
    )
    assert "owner" not in visible
    assert "token" not in visible
    assert "/private/bot" not in visible
    assert "secret=value" not in visible
