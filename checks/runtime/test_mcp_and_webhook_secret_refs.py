from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config import credentials
from gideon.core.config.loader import AppConfig, config_path
from gideon.extensions.providers import mcp_instances
from gideon.interfaces.dashboard.handlers import hooks


def test_mcp_credentials_are_owner_refs_and_resolve_at_consumer(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")

    instance = mcp_instances.create_instance(
        "weather",
        {
            "transport": "sse",
            "endpoint": "https://mcp.example/tools",
            "env": {"WEATHER_API_TOKEN": "weather-first-check-value", "REGION": "eu"},
            "headers": {"Authorization": "Bearer weather-first-check-value"},
        },
    )
    assert instance.id == "weather"
    path = home / "mcp.json"
    stored = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["weather"]
    serialized = json.dumps(stored)
    assert "weather-first-check-value" not in serialized
    assert stored["env"]["WEATHER_API_TOKEN"].startswith("{{secret:GIDEON_SECRET_MCP_")
    assert stored["headers"]["Authorization"].startswith("{{secret:GIDEON_SECRET_MCP_")
    assert mcp_instances.resolve_server_credentials("weather", stored)["env"] == {
        "WEATHER_API_TOKEN": "weather-first-check-value",
        "REGION": "eu",
    }

    old_reference = stored["env"]["WEATHER_API_TOKEN"]
    backend_key = old_reference.removeprefix("{{secret:").removesuffix("}}")
    credentials.put_secret_value(backend_key, "weather-stable-reference-rotation")
    reloaded = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["weather"]
    assert reloaded["env"]["WEATHER_API_TOKEN"] == old_reference
    assert (
        mcp_instances.resolve_server_credentials("weather", reloaded)["env"][
            "WEATHER_API_TOKEN"
        ]
        == "weather-stable-reference-rotation"
    )

    mcp_instances.update_instance(
        "weather",
        config={
            "transport": "sse",
            "endpoint": "https://mcp.example/tools",
            "env": {"WEATHER_API_TOKEN": "weather-rotated-check-value", "REGION": "eu"},
            "headers": {"Authorization": "Bearer weather-rotated-check-value"},
        },
    )
    stored = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["weather"]
    assert "weather-rotated-check-value" not in json.dumps(stored)
    assert credentials.get_secret_value(old_reference) == ""
    assert mcp_instances.resolve_server_credentials("weather", stored)["headers"] == {
        "Authorization": "Bearer weather-rotated-check-value"
    }

    try:
        mcp_instances.resolve_server_credentials("another-server", stored)
    except ValueError:
        pass
    else:
        raise AssertionError("a different MCP owner resolved this server's credentials")


def test_webhook_token_is_persisted_by_reference_and_used_by_real_handler(
    tmp_path, monkeypatch
):
    home = tmp_path / "gideon-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    token = "webhook-owner-check-value"
    config = AppConfig.load()
    config.hooks["webhook_token"] = token
    config.save()

    stored = json.loads(config_path().read_text(encoding="utf-8"))
    reference = stored["hooks"]["webhook_token"]
    assert reference != token
    assert token not in json.dumps(stored)
    assert credentials.get_secret_value(reference) == token

    async def journey():
        async def verify(request):
            return web.Response(
                status=204 if hooks._verify_hook_token(request) else 401
            )

        app = web.Application()
        app.router.add_post("/verify", verify)
        async with TestServer(app) as server:
            async with TestClient(server) as client:
                accepted = await client.post(
                    "/verify", headers={"Authorization": f"Bearer {token}"}
                )
                rejected = await client.post(
                    "/verify", headers={"Authorization": "Bearer wrong-owner-value"}
                )
                return accepted.status, rejected.status

    assert asyncio.run(journey()) == (204, 401)
