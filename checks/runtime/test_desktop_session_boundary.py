"""Desktop sessions use the actual local socket and ordinary owner authentication."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers.auth import api_auth_logout, _set_session_cookie
from gideon.interfaces.dashboard.origin import auth_is_off
from gideon.interfaces.dashboard.server import _dashboard_csp
from gideon.security.auth.modes import AuthConfig, AuthMode


@pytest.mark.asyncio
async def test_dynamic_port_login_cookie_authenticates_and_logout_revokes(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "desktop")
    # The retired blanket switch must not bypass any owner or app gate.
    monkeypatch.setenv("GIDEON_DEV_NO_AUTH", "1")
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"desktop-boundary-session-key")
    token_auth.revoke_all_sessions()
    token = token_auth.generate_token("desktop-owner", kind="desktop")
    async def identity(request):
        return web.json_response({"user": request["user"], "port": token_auth.served_port(request)})
    async def login(request):
        response = web.json_response({"ok": True})
        _set_session_cookie(request, response, token, 3600)
        return response
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=0)])
    app["port"] = 0
    app["allowed_origins"] = set()
    app.router.add_get("/api/status", identity)
    app.router.add_post("/api/auth/logout", api_auth_logout)
    app.router.add_get("/api/local-login", login)
    client = TestClient(TestServer(app))
    await client.start_server()
    port = client.server.port
    try:
        assert (await client.get("/api/status")).status == 403
        response = await client.get("/api/local-login", headers={"Authorization": f"Bearer {token}"})
        assert response.status == 200
        cookie = f"gideon_token_{port}"
        assert cookie in response.cookies
        assert response.cookies[cookie]["httponly"]
        headers = {"Cookie": f"{cookie}={token}", "Host": "attacker.invalid:1"}
        response = await client.get("/api/status", headers=headers)
        assert response.status == 200
        assert (await response.json())["port"] == port
        response = await client.post("/api/auth/logout", headers=headers)
        assert response.status == 200 and (await response.json())["revoked"]
        assert (await client.get("/api/status", headers=headers)).status == 403
        assert (await client.get("/api/status", headers={"Authorization": f"Bearer {token}"})).status == 403
        assert "Quit Gideon and open it again" in await (await client.get("/")).text()
        assert f"ws://localhost:{port}" in _dashboard_csp(port)
        assert "localhost:*" not in _dashboard_csp(port)
    finally:
        await client.close()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()


def test_explicit_development_auth_none_is_preserved(monkeypatch):
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    assert auth_is_off(AuthConfig.from_env())
    monkeypatch.setenv("GIDEON_AUTH_MODE", "local_token")
    monkeypatch.setenv("GIDEON_DEV_NO_AUTH", "1")
    assert not auth_is_off(AuthConfig.from_env())
