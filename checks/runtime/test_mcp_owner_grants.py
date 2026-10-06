"""Owner consent gates real MCP probe and ACP configuration paths."""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import gideon


@pytest.mark.asyncio
async def test_owner_allow_gates_real_stdio_probe_and_definition_edits(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    marker = tmp_path / "started.txt"
    changed_marker = tmp_path / "changed-started.txt"
    server_script = tmp_path / "mcp_fixture.py"
    server_script.write_text(
        textwrap.dedent(f"""
            import os
            import sys

            sys.path.insert(0, {str(Path(gideon.__file__).resolve().parent.parent)!r})
            from gideon.integrations.mcp_core import run_mcp_core_server

            with open(os.environ["MARKER"], "a", encoding="utf-8") as handle:
                handle.write("started\\n")
            run_mcp_core_server()
            """),
        encoding="utf-8",
    )
    spec = {
        "command": sys.executable,
        "args": [str(server_script)],
        "env": {"MARKER": str(marker)},
    }
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"local-fixture": spec}}), encoding="utf-8"
    )

    from gideon.extensions.apps.manifest import AppManifest
    from gideon.integrations.mcp_discovery import _server_from_spec, probe_server
    from gideon.interfaces.dashboard.api_version_gate import api_version_middleware
    from gideon.interfaces.dashboard.handlers.mcp import (
        api_mcp_server_allow,
        api_mcp_server_detail,
    )
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        token_auth_middleware,
    )
    from gideon.security import mcp_grants

    manifest = AppManifest.from_dict(
        {
            "name": "fixture-app",
            "version": "1.0.0",
            "displayName": "Fixture",
            "description": "Fixture app",
            "mcpServers": {"local": {"command": sys.executable}},
        }
    )
    assert any("owner-managed" in error for error in manifest.validate())

    server = _server_from_spec("local-fixture", spec, "mcp.json")
    blocked = await probe_server(server)
    assert blocked.status == mcp_grants.WAITING
    assert blocked.tools == []
    assert not marker.exists()

    agent_config = home / "agents" / "gideon.json"
    app = web.Application(
        middlewares=[
            api_version_middleware(),
            token_auth_middleware(
                internal_paths=frozenset(),
                mixed_internal_paths=frozenset(),
                port=0,
            ),
        ]
    )
    app.router.add_post("/api/mcp/servers/{name}/allow", api_mcp_server_allow)
    app.router.add_delete("/api/mcp/servers/{name}", api_mcp_server_detail)
    owner_token = generate_token("fixture-owner", kind="browser")
    headers = {
        "Authorization": f"Bearer {owner_token}",
        "X-Gideon-API-Version": "1",
    }
    from gideon.engine.agent import rebuild_agent_config

    await asyncio.to_thread(rebuild_agent_config)
    installed = json.loads(agent_config.read_text(encoding="utf-8"))
    assert "local-fixture" not in installed.get("mcpServers", {})
    async with TestClient(TestServer(app)) as client:
        grant_data = {
            "revision": mcp_grants.revision(server),
            "question": mcp_grants.question(server),
            "confirmed": True,
        }
        response = await client.post(
            "/api/mcp/servers/local-fixture/allow", headers=headers, json=grant_data
        )
        assert response.status == 200
        assert (await response.json())["allowed"] is True

        assert mcp_grants.allowed(server)
        await asyncio.to_thread(rebuild_agent_config)
        installed = json.loads(agent_config.read_text(encoding="utf-8"))
        assert "local-fixture" in installed.get("mcpServers", {})
        allowed = await probe_server(server)
        assert allowed.status == "ok"
        assert allowed.tools
        assert marker.read_text(encoding="utf-8").splitlines() == ["started"]

        spec["env"] = {"MARKER": str(changed_marker)}
        (home / "mcp.json").write_text(
            json.dumps({"mcpServers": {"local-fixture": spec}}), encoding="utf-8"
        )
        response = await client.post(
            "/api/mcp/servers/local-fixture/allow", headers=headers, json=grant_data
        )
        assert response.status == 409
        changed = _server_from_spec("local-fixture", spec, "mcp.json")
        blocked_after_edit = await probe_server(changed)
        assert blocked_after_edit.status == mcp_grants.WAITING
        assert not changed_marker.exists()
        assert not mcp_grants.allowed(changed)
        await asyncio.to_thread(rebuild_agent_config)
        installed = json.loads(agent_config.read_text(encoding="utf-8"))
        assert "local-fixture" not in installed.get("mcpServers", {})

        changed_grant = {
            "revision": mcp_grants.revision(changed),
            "question": mcp_grants.question(changed),
            "confirmed": True,
        }
        response = await client.post(
            "/api/mcp/servers/local-fixture/allow", headers=headers, json=changed_grant
        )
        assert response.status == 200
        assert mcp_grants.allowed(changed)
        response = await client.delete(
            "/api/mcp/servers/local-fixture", headers=headers
        )
        assert response.status == 200
        assert not mcp_grants.allowed(changed)
