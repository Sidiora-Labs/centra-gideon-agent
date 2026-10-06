"""Real owner-authenticated MCP import keeps source credentials off the wire."""

from __future__ import annotations

import json
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_selected_import_is_opaque_and_safe_projection_never_returns_values(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    source = {
        "url": "https://mcp.example.test/rpc?api_key=url-secret-value",
        "transport": "streamable_http",
        "args": ["--token=argument-secret-value", "--mode", "safe"],
        "env": {"SERVICE_TOKEN": "env-secret-value\nsecond-line"},
        "headers": {"Authorization": "Bearer header-secret-value"},
    }
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"remote-fixture": source}}), encoding="utf-8"
    )

    from gideon.interfaces.dashboard.api_version_gate import api_version_middleware
    from gideon.interfaces.dashboard.handlers.mcp import (
        api_mcp_apply,
        api_mcp_importable,
        api_mcp_server_detail,
    )
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        token_auth_middleware,
    )

    app = web.Application(
        middlewares=[
            api_version_middleware(),
            token_auth_middleware(
                internal_paths=frozenset(), mixed_internal_paths=frozenset(), port=0
            ),
        ]
    )
    app.router.add_get("/api/mcp/importable", api_mcp_importable)
    app.router.add_post("/api/mcp/apply", api_mcp_apply)
    app.router.add_put("/api/mcp/servers/{name}", api_mcp_server_detail)
    app.router.add_delete("/api/mcp/servers/{name}", api_mcp_server_detail)
    headers = {
        "Authorization": f"Bearer {generate_token('fixture-owner', kind='browser')}",
        "X-Gideon-API-Version": "1",
    }

    async with TestClient(TestServer(app)) as client:
        listed = await client.get("/api/mcp/importable", headers=headers)
        assert listed.status == 200
        listing_text = await listed.text()
        assert all(
            secret not in listing_text
            for secret in (
                "url-secret-value",
                "argument-secret-value",
                "env-secret-value",
                "second-line",
                "header-secret-value",
            )
        )
        candidate = (await listed.json())["servers"][0]
        assert re.fullmatch(r"[0-9a-f]{16}", candidate["id"])
        assert candidate["env"] == [{"name": "SERVICE_TOKEN", "configured": True}]
        assert candidate["headers"] == [{"name": "Authorization", "configured": True}]
        assert candidate["display_url"] and candidate["display_args"]

        imported = await client.post(
            "/api/mcp/apply", json={"import_id": candidate["id"]}, headers=headers
        )
        assert imported.status == 200
        payload = await imported.json()
        server = payload["server"]
        assert server["allowed"] is False
        assert server["allowRevision"] and server["allowQuestion"]
        import_text = json.dumps(payload)
        assert all(
            secret not in import_text
            for secret in (
                "url-secret-value",
                "argument-secret-value",
                "env-secret-value",
                "second-line",
                "header-secret-value",
            )
        )

        from gideon.core.config.credentials import get_secret_value

        canonical = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
        stored = canonical["mcpServers"]["remote-fixture"]
        env_ref = stored["env"]["SERVICE_TOKEN"]
        header_ref = stored["headers"]["Authorization"]
        assert env_ref.startswith("{{secret:GIDEON_SECRET_MCP_")
        assert header_ref.startswith("{{secret:GIDEON_SECRET_MCP_")
        assert get_secret_value(env_ref[9:-2]) == "env-secret-value\nsecond-line"
        assert get_secret_value(header_ref[9:-2]) == "Bearer header-secret-value"

        edited = await client.put(
            "/api/mcp/servers/remote-fixture",
            json={"transport": "sse"},
            headers=headers,
        )
        assert edited.status == 200
        edit_payload = await edited.json()
        edit_text = json.dumps(edit_payload)
        assert all(
            secret not in edit_text
            for secret in (
                "url-secret-value",
                "argument-secret-value",
                "env-secret-value",
                "second-line",
                "header-secret-value",
            )
        )
        after_edit = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
        assert (
            after_edit["mcpServers"]["remote-fixture"]["env"]["SERVICE_TOKEN"]
            == env_ref
        )
        assert (
            after_edit["mcpServers"]["remote-fixture"]["headers"]["Authorization"]
            == header_ref
        )
        from gideon.extensions.providers.mcp_instances import resolve_server_credentials

        stored_spec = after_edit["mcpServers"]["remote-fixture"]
        assert (
            resolve_server_credentials("remote-fixture", stored_spec)["args"]
            == source["args"]
        )
        assert "argument-secret-value" not in json.dumps(stored_spec)

        deleted = await client.delete(
            "/api/mcp/servers/remote-fixture", headers=headers
        )
        assert deleted.status == 200
        assert not (home / "mcp.json").exists() or "remote-fixture" not in json.loads(
            (home / "mcp.json").read_text(encoding="utf-8")
        ).get("mcpServers", {})
        assert get_secret_value(env_ref[9:-2]) == ""
        assert get_secret_value(header_ref[9:-2]) == ""
