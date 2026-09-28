"""Owner-visible self-hosted sessions exercise the durable production registry."""

from __future__ import annotations

import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import session_store as ss
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import devices

PORT = 10000
COOKIE = f"gideon_token_{PORT}"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    isolated_home = tmp_path / "home"
    isolated_config = isolated_home / ".gideon"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("GIDEON_HOME", str(isolated_config))
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    assert ss.config_dir() == isolated_config
    return isolated_config


def _app() -> web.Application:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    devices.register_device_routes(app)
    return app


@pytest.mark.asyncio
async def test_signed_in_list_contains_browser_cli_and_pair_but_never_apps(home):
    browser = token_auth.generate_token(
        "owner", ttl_seconds=3600, kind="browser", label="Firefox", client_ip="192.0.2.8"
    )
    cli = token_auth.generate_token(
        "owner", ttl_seconds=3600, kind="cli", label="gideon token", client_ip="127.0.0.1"
    )
    app_token = token_auth.generate_token("owner", ttl_seconds=3600, app="notes")
    pair = token_auth.generate_token(
        "paired-device", ttl_seconds=3600, kind="device", label="Phone", client_ip="192.0.2.9"
    )
    paired_nonce = token_auth.token_nonce(pair)
    ss.attach_device(
        paired_nonce,
        ss.DeviceInfo(id="phone-1", name="Phone", kind="mobile", minted_at=time.time()),
    )

    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/devices", cookies={COOKIE: browser})
        assert response.status == 200
        rows = (await response.json())["devices"]

    by_kind = {row["kind"]: row for row in rows}
    assert set(by_kind) == {"browser", "cli", "mobile"}
    assert by_kind["browser"]["current"] is True
    assert by_kind["cli"]["current"] is False
    assert by_kind["browser"]["name"] == "Firefox"
    assert by_kind["browser"]["ip"] == "192.0.2.8"
    assert by_kind["mobile"]["ip"] == "192.0.2.9"
    assert all("nonce" not in json.dumps(row).lower() for row in rows)
    assert token_auth.token_nonce(app_token) not in json.dumps(rows)
    assert token_auth.token_nonce(cli) not in json.dumps(rows)


@pytest.mark.asyncio
async def test_confirmation_gates_revoke_others_and_retains_current(home):
    current = token_auth.generate_token("owner", ttl_seconds=3600, kind="browser")
    other = token_auth.generate_token("owner", ttl_seconds=3600, kind="browser", label="Other")
    other_nonce = token_auth.token_nonce(other)

    async with TestClient(TestServer(_app())) as client:
        refused = await client.post(
            "/api/devices/revoke-others", json={"confirmed": False}, cookies={COOKIE: current}
        )
        assert refused.status == 400
        assert other_nonce in ss.load_sessions()

        accepted = await client.post(
            "/api/devices/revoke-others", json={"confirmed": True}, cookies={COOKIE: current}
        )
        assert accepted.status == 200
        assert (await accepted.json()) == {"ok": True, "revoked": 1}

    assert token_auth.validate_token(current, use_session_exp=True)[0] is True
    valid, _, reason = token_auth.validate_token(other, use_session_exp=True)
    assert valid is False
    assert reason == "revoked"


@pytest.mark.parametrize("kind", ["browser", "cli", "app"])
def test_each_session_pool_keeps_its_own_oldest_member(home, kind):
    first = token_auth.generate_token("owner", ttl_seconds=3600, kind=kind)
    created = [
        token_auth.generate_token("owner", ttl_seconds=3600, kind=kind)
        for _ in range(token_auth.MAX_CONCURRENT_NONCES)
    ]
    if kind == "app":
        other = token_auth.generate_token("owner", ttl_seconds=3600, kind="browser")
        assert token_auth.validate_token(other, use_session_exp=True)[0] is True
    valid, _, reason = token_auth.validate_token(first, use_session_exp=True)
    assert valid is False
    assert reason == "evicted"
    assert all(token_auth.token_nonce(token) in ss.load_sessions() for token in created)
