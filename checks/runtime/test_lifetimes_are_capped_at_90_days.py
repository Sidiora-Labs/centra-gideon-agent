"""The shared lifetime rule refuses new excesses and caps legacy session claims."""

from __future__ import annotations

import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import session_store as ss
from gideon.interfaces.dashboard import token_auth as ta
from gideon.interfaces.dashboard.handlers.core import api_token_local
from gideon.security.auth import lifetimes


@pytest.fixture()
def home(tmp_path, monkeypatch):
    isolated_home = tmp_path / "home"
    isolated_config = isolated_home / ".gideon"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("GIDEON_HOME", str(isolated_config))
    ta.use_persistent_secret()
    ta.revoke_all_sessions()
    assert ss.config_dir() == isolated_config
    return isolated_config


def test_one_shared_policy_accepts_90_days_and_refuses_excessive_values():
    assert lifetimes.parse_lifetime("90d") == 90 * 86400
    assert lifetimes.parse_lifetime("91d") is None
    assert ta.parse_duration("2160h") == lifetimes.MAX_SESSION_TTL_SECS
    assert ta.parse_duration("2161h") is None
    assert (
        ta.parse_config_duration(
            "91d", default_secs=lifetimes.DEFAULT_BROWSER_SESSION_TTL_SECS
        )
        == lifetimes.DEFAULT_BROWSER_SESSION_TTL_SECS
    )
    with pytest.raises(ValueError, match="90 days"):
        ta.generate_token("owner", ttl_seconds=lifetimes.MAX_SESSION_TTL_SECS + 1)


def test_a_legacy_long_claim_expires_at_issued_at_plus_90_days(home):
    issued_at = time.time() - 91 * 86400
    nonce = "legacy-long-session"
    payload = {
        "sub": "owner",
        "exp": issued_at + ta.LINK_WINDOW_SECS,
        "session_exp": issued_at + 365 * 86400,
        "iat": issued_at,
        "nonce": nonce,
    }
    encoded = ta._b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    token = f"{encoded}.{ta._sign(ta._b64url_decode(encoded))}"
    ss.sessions_path().parent.mkdir(parents=True, exist_ok=True)
    ss.sessions_path().write_text(
        json.dumps(
            {
                "sessions": {
                    nonce: {
                        "exp": payload["session_exp"],
                        "issuer": "local",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    valid, _, reason = ta.validate_token(token, use_session_exp=True)

    assert valid is False
    assert reason == "token expired"


def test_legacy_store_records_are_capped_even_before_a_token_claim_is_checked(home):
    issued_at = time.time() - 10
    expiry = issued_at + 365 * 86400
    ss.sessions_path().write_text(
        json.dumps(
            {
                "sessions": {
                    "legacy": {
                        "exp": expiry,
                        "issuer": "local",
                        "minted_at": issued_at,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert ss.load_sessions()["legacy"] == pytest.approx(
        issued_at + lifetimes.MAX_SESSION_TTL_SECS
    )


@pytest.mark.asyncio
async def test_local_cli_issuer_refuses_excessive_ttl_and_reports_the_policy(home):
    app = web.Application()
    app["local_secret"] = "local-only-test-secret"
    app.router.add_get("/api/token/local", api_token_local)
    async with TestClient(TestServer(app)) as client:
        refused = await client.get(
            "/api/token/local?ttl=91d",
            headers={"X-Local-Secret": "local-only-test-secret"},
        )
        assert refused.status == 400
        assert (await refused.json())[
            "maximum_seconds"
        ] == lifetimes.MAX_SESSION_TTL_SECS

        issued = await client.get(
            "/api/token/local?ttl=1h",
            headers={"X-Local-Secret": "local-only-test-secret"},
        )
        body = await issued.json()
        assert issued.status == 200
        assert body["expires_in"] == 3600
        assert body["maximum_lifetime_seconds"] == lifetimes.MAX_SESSION_TTL_SECS
        assert body["default_lifetime_seconds"] == lifetimes.MAX_SESSION_TTL_SECS
        row = next(iter(ss.load_session_records().values()))
        assert (row.kind, row.pool, row.ip) == ("cli", "token", "127.0.0.1")
