from __future__ import annotations

import asyncio
import json
import socket

import pytest

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers import tool_prefs


@pytest.mark.asyncio
async def test_remote_mcp_disable_is_canonical_and_sibling_stays_callable(
    tmp_path, monkeypatch
):
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    home = config_dir()
    home.mkdir(parents=True, exist_ok=True)

    from gideon.core.config.loader import AppConfig

    operator_config = AppConfig.load()
    operator_config.security.egress.allow_hosts = ["127.0.0.1"]
    operator_config.save()

    server = FastMCP("MCP disable regression", stateless_http=True, json_response=True)

    @server.tool()
    def disabled_tool() -> str:
        return "should not run"

    @server.tool()
    def sibling_tool(value: str) -> str:
        return value.upper()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
    application = server.streamable_http_app()
    process = uvicorn.Server(
        uvicorn.Config(application, log_level="critical", lifespan="on")
    )
    server_task = asyncio.create_task(process.serve(sockets=[listener]))

    from gideon.engine.agents.native.runtime import _ToolInventory
    from gideon.integrations import mcp_client
    from gideon.integrations.tool_providers import registry
    from gideon.security import mcp_grants
    from gideon.security.approval_answer import OWNER, Principal

    name = "mcp05-disable"
    spec = {
        "url": endpoint,
        "transport": "streamable_http",
        "disabledTools": ["disabled_tool"],
    }
    server_doc = {**spec, "name": name, "source": "mcp.json"}
    grant = False
    try:
        for _ in range(200):
            if process.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)
        assert process.started

        mcp_grants.give(
            server_doc, Principal(OWNER, "mcp05-test-owner", "mcp05-test-tenant")
        )
        grant = True
        (home / "mcp.json").write_text(
            json.dumps({"mcpServers": {name: spec}}), encoding="utf-8"
        )
        canonical_disabled = f"mcp/{name}/disabled_tool"
        assert canonical_disabled in tool_prefs.load_disabled()
        assert tool_prefs.is_disabled(name, canonical_disabled)
        assert not tool_prefs.is_disabled(name, f"mcp/{name}/sibling_tool")

        providers = registry.list_providers()
        catalog = await registry.resolve_tool_catalog(providers)
        inventory = await _ToolInventory.discover(providers, unattended=False)
        names = {definition.name for definition in inventory.definitions}
        assert f"mcp/{name}/disabled_tool" not in names
        assert f"mcp/{name}/sibling_tool" in names

        provider = catalog.providers[f"mcp/{name}/sibling_tool"]
        refused = await provider.invoke(f"mcp/{name}/disabled_tool", {})
        assert refused.success is False
        result = await provider.invoke(
            f"mcp/{name}/sibling_tool", {"value": "still live"}
        )
        assert result.success is True
        assert "STILL LIVE" in result.output
    finally:
        if grant:
            mcp_grants.revoke(server_doc)
        (home / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        if mcp_client._registry is not None:
            await mcp_client._registry.shutdown_all()
        mcp_client._registry = None
        registry.list_providers()
        process.should_exit = True
        await asyncio.wait_for(server_task, 10)
        listener.close()
