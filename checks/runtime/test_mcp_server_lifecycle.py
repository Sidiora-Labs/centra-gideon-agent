"""MCP secret references remain owner-bound through update and cleanup."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def test_server_credentials_are_owner_bound_and_purge_isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))

    from gideon.core.config.secret_refs import (
        ForeignSecretReference,
        SecretOwner,
        purge,
    )
    from gideon.extensions.providers.mcp_instances import resolve_server_credentials
    from gideon.integrations.mcp_secret_refs import store_server_credentials

    alpha = store_server_credentials(
        "alpha",
        {
            "url": "https://alpha.example.test/mcp",
            "transport": "streamable_http",
            "headers": {"Authorization": "Bearer alpha-secret"},
            "env": {"SERVICE_TOKEN": "alpha-env-secret"},
        },
    )
    beta = store_server_credentials(
        "beta",
        {
            "url": "https://beta.example.test/mcp",
            "transport": "sse",
            "headers": {"Authorization": "Bearer beta-secret"},
            "env": {"SERVICE_TOKEN": "beta-env-secret"},
        },
    )

    assert alpha["headers"]["Authorization"].startswith(
        "{{secret:GIDEON_SECRET_MCP_ALPHA_"
    )
    assert beta["headers"]["Authorization"].startswith(
        "{{secret:GIDEON_SECRET_MCP_BETA_"
    )
    assert (
        resolve_server_credentials("alpha", alpha)["headers"]["Authorization"]
        == "Bearer alpha-secret"
    )
    with pytest.raises(ForeignSecretReference):
        resolve_server_credentials("beta", {"headers": alpha["headers"]})

    purge([SecretOwner("MCP", "alpha").prefix])
    assert (
        resolve_server_credentials("beta", beta)["headers"]["Authorization"]
        == "Bearer beta-secret"
    )
    with pytest.raises(ValueError):
        resolve_server_credentials("alpha", alpha)


@pytest.mark.asyncio
async def test_masked_provider_card_round_trip_preserves_owner_secret(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")

    from gideon.extensions.apps.manifest import AppManifest, ProviderConfig
    from gideon.extensions.providers import registry as provider_registry
    from gideon.extensions.providers.instance_routes import register_instance_routes
    from gideon.integrations.mcp_discovery import McpServerInfo

    secret = "short-token-value"
    (home / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cli": {
                        "command": f"runner --api-token={secret}",
                        "args": ["--header", f"Authorization: Bearer {secret}"],
                        "env": {"API_TOKEN": secret},
                    },
                    "remote": {
                        "url": f"https://gateway.example.test/path?token={secret}"
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    provider_registry.reset_provider_registry()
    registry = provider_registry.get_provider_registry()
    schema = {
        "type": "object",
        "properties": {
            "transport": {"type": "string", "enum": ["stdio", "sse"]},
            "command": {"type": "string"},
            "args": {"type": "string"},
            "endpoint": {"type": "string"},
        },
    }
    registry.register(
        AppManifest(
            name="mcp-tools",
            provider=ProviderConfig(
                type="tool",
                implementation="gideon.extensions.providers.mcp_instances:list_instances",
                multiInstance=True,
                settingsSchema=schema,
            ),
        )
    )
    app = web.Application()
    register_instance_routes(app)
    async with TestClient(TestServer(app)) as client:
        listed = await client.get("/api/providers/mcp-tools/instances")
        assert listed.status == 200, await listed.text()
        instances = (await listed.json())["instances"]
        instance = next(item for item in instances if item["id"] == "cli")
        remote = next(item for item in instances if item["id"] == "remote")
        assert secret not in await listed.text()
        assert remote["config"]["endpoint"] == "https://gateway.example.test/…"
        assert "[REDACTED: credential]" in instance["config"]["command"]
        assert "[REDACTED: credential]" in instance["config"]["args"]
        revision = instance["revision"]
        config = {
            **instance["config"],
            "args": instance["config"]["args"] + " --verbose",
        }

        saved = await client.put(
            "/api/providers/mcp-tools/instances/cli",
            json={"config": config},
            headers={"If-Match": revision},
        )
        assert saved.status == 200, await saved.text()
        assert secret not in await saved.text()
        saved_instance = (await saved.json())["instance"]
        raw = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
        spec = raw["mcpServers"]["cli"]
        from gideon.extensions.providers.mcp_instances import resolve_server_credentials

        assert resolve_server_credentials("cli", spec)["env"]["API_TOKEN"] == secret
        assert spec["args"] == [
            "--header",
            f"Authorization: Bearer {secret}",
            "--verbose",
        ]

        stale = await client.put(
            "/api/providers/mcp-tools/instances/cli",
            json={"config": config},
            headers={"If-Match": revision},
        )
        assert stale.status == 409

        moved_config = {
            **saved_instance["config"],
            "args": f"--header --renamed=[REDACTED: credential] --verbose",
        }
        moved = await client.put(
            "/api/providers/mcp-tools/instances/cli",
            json={"config": moved_config},
            headers={"If-Match": saved_instance["revision"]},
        )
        assert moved.status == 409
        assert "stale_write" in await moved.text()
        raw_after = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
        assert raw_after["mcpServers"]["cli"] == spec

    command_projection = McpServerInfo(
        name="projection",
        command=f"runner --api-token={secret}",
        args=["--header", f"Authorization: Bearer {secret}"],
        source="mcp.json",
    ).to_dict()
    assert secret not in json.dumps(command_projection)
    url_projection = McpServerInfo(
        name="remote",
        url=f"https://gateway.example.test/path?token={secret}",
        source="mcp.json",
    ).to_dict()
    assert secret not in json.dumps(url_projection)
    provider_registry.reset_provider_registry()


def test_probe_status_is_bound_to_current_server_definition(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))

    from gideon.integrations.mcp_discovery import (
        McpServerInfo,
        _cache_probe,
        _get_cached,
        _probe_cache,
    )
    from gideon.security.approval_answer import OWNER, Principal
    from gideon.security.mcp_grants import give, revoke

    _probe_cache.clear()
    original = McpServerInfo(
        name="definition-bound",
        command="python",
        args=["server.py"],
        source="mcp.json",
    )
    give(original, Principal(OWNER, "mcp-definition-test-owner"))
    original.status = "ok"
    original.tools = [{"name": "current_tool", "description": ""}]
    _cache_probe(original)
    try:
        assert _get_cached(original) == ("ok", original.tools, "")
        changed = McpServerInfo(
            name=original.name,
            command="python",
            args=["different-server.py"],
            source="mcp.json",
        )
        assert _get_cached(changed) == ("unknown", [], "")
    finally:
        revoke(original)
        _probe_cache.clear()
