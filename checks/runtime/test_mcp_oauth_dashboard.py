from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_mcp_oauth_status_uses_authenticated_owner_route_without_returning_tokens(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")

    spec = {"url": "https://catalog.example/mcp", "transport": "streamable_http"}
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"catalog-index": spec}}),
        encoding="utf-8",
    )

    from gideon.interfaces.dashboard.api_version_gate import api_version_middleware
    from gideon.interfaces.dashboard.handlers.mcp import api_mcp_oauth_status
    from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
    from gideon.security import mcp_grants
    from gideon.security.approval_answer import OWNER, Principal

    server = {**spec, "name": "catalog-index", "source": "mcp.json"}
    mcp_grants.give(server, Principal(OWNER, "fixture-owner"))
    app = web.Application(
        middlewares=[
            api_version_middleware(),
            token_auth_middleware(internal_paths=frozenset(), mixed_internal_paths=frozenset(), port=0),
        ]
    )
    app.router.add_get("/api/mcp/servers/{name}/oauth", api_mcp_oauth_status)
    owner = generate_token("fixture-owner", kind="browser")
    app_token = generate_token("untrusted-app", app="fixture-app")
    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/api/mcp/servers/catalog-index/oauth",
            headers={"Authorization": f"Bearer {owner}", "X-Gideon-API-Version": "1"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["state"] == "signin"
        assert "access_token" not in body
        assert "refresh_token" not in body

        denied = await client.get(
            "/api/mcp/servers/catalog-index/oauth",
            headers={"Authorization": f"Bearer {app_token}", "X-Gideon-API-Version": "1"},
        )
        assert denied.status == 403
