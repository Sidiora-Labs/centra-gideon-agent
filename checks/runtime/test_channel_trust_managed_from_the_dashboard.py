from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations import channel_trust
from gideon.integrations.channel_transports import register_transport, unregister_transport
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport
from gideon.interfaces.dashboard.handlers.channel_trust import (
    api_channel_trust,
    api_channel_trust_pairing,
    api_channel_trust_pairing_cancel,
    api_channel_trust_policy,
    api_channel_trust_track,
    api_channel_trust_untrack,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    transport = ReferenceEchoTransport()
    register_transport(transport)
    yield transport
    unregister_transport(transport.name)


@pytest.mark.asyncio
async def test_owner_routes_change_the_same_inbound_policy_and_pairing_store(isolated):
    app = web.Application()
    app.router.add_get("/api/channels/trust", api_channel_trust)
    app.router.add_put("/api/channels/trust/{provider}/policies", api_channel_trust_policy)
    app.router.add_post("/api/channels/trust/{provider}/pairing", api_channel_trust_pairing)
    app.router.add_delete("/api/channels/trust/{provider}/pairing", api_channel_trust_pairing_cancel)
    app.router.add_post("/api/channels/trust/{provider}/channels", api_channel_trust_track)
    app.router.add_delete("/api/channels/trust/{provider}/channels/{channel_id}", api_channel_trust_untrack)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/channels/trust")
        assert response.status == 200
        assert any(row["provider"] == "reference-echo" for row in (await response.json())["providers"])

        refused = await client.put("/api/channels/trust/reference-echo/policies", json={"dm": "open"})
        assert refused.status == 409
        accepted = await client.put("/api/channels/trust/reference-echo/policies", json={"dm": "open", "confirm_open": True})
        assert accepted.status == 200
        assert channel_trust.guard_inbound(None, "reference-echo", "unpaired", text="hello").allowed

        closed = await client.put("/api/channels/trust/reference-echo/policies", json={"dm": "owner_only"})
        assert closed.status == 200
        verdict = channel_trust.guard_inbound(None, "reference-echo", "unpaired", text="hello")
        assert not verdict.allowed and not verdict.canned_reply

        issued = await client.post("/api/channels/trust/reference-echo/pairing")
        assert issued.status == 200 and issued.headers["Cache-Control"] == "no-store"
        code = (await issued.json())["code"]
        assert code.isdigit() and len(code) == 8
        assert code not in json.dumps(channel_trust._read_store())
        cancelled = await client.delete("/api/channels/trust/reference-echo/pairing")
        assert cancelled.status == 200
        assert not channel_trust._pairing_code_outstanding("reference-echo")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_seen_groups_can_be_tracked_and_unknown_reply_shares_persisted_throttle(isolated):
    transport = isolated
    first = channel_trust.guard_inbound(None, transport.name, "stranger", channel_id="room-a", is_dm=False, text="hello")
    second = channel_trust.guard_inbound(None, transport.name, "stranger", channel_id="room-a", is_dm=False, text="hello")
    assert not first.allowed and not second.allowed
    assert channel_trust.list_seen_channels(transport.name)[0]["channel_id"] == "room-a"

    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    app.router.add_post("/api/channels/trust/{provider}/channels", api_channel_trust_track)
    app.router.add_delete("/api/channels/trust/{provider}/channels/{channel_id}", api_channel_trust_untrack)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        tracked = await client.post("/api/channels/trust/reference-echo/channels", json={"channel_id": "room-a", "name": "General"})
        assert tracked.status == 200 and channel_trust.is_tracked_channel(transport.name, "room-a")
        untracked = await client.delete("/api/channels/trust/reference-echo/channels/room-a")
        assert untracked.status == 200 and not channel_trust.is_tracked_channel(transport.name, "room-a")

        first_dm = channel_trust.guard_inbound(None, transport.name, "stranger", text="hello")
        second_dm = channel_trust.guard_inbound(None, transport.name, "stranger", text="hello")
        assert first_dm.canned_reply
        assert not second_dm.canned_reply
    finally:
        await client.close()
