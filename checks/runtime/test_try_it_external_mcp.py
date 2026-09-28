"""Try It resolves and invokes external MCP tools by canonical name."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_try_it_uses_the_agent_catalog_without_a_provider_selector(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    from gideon.integrations import mcp_client

    if mcp_client._registry is not None:
        await mcp_client._registry.shutdown_all()
    mcp_client._registry = None
    from gideon.core.config.loader import config_dir

    script = tmp_path / "mcp_try_it.py"
    script.write_text(
        textwrap.dedent(
            """
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("try-it-fixture")

            @mcp.tool()
            def echo(value: str) -> str:
                \"\"\"Return the supplied value.\"\"\"
                return value

            if __name__ == "__main__":
                mcp.run()
            """
        ),
        encoding="utf-8",
    )
    spec = {"command": sys.executable, "args": [str(script)]}
    config = config_dir() / "mcp.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"mcpServers": {"try-demo": spec}}), encoding="utf-8")
    from gideon.integrations.mcp_discovery import McpServerInfo
    from gideon.security import mcp_grants
    from gideon.security.approval_answer import OWNER, Principal

    mcp_grants.give(
        McpServerInfo(
            name="try-demo",
            command=sys.executable,
            args=[str(script)],
            source="mcp.json",
        ),
        Principal(OWNER, "mcp04-try-owner"),
    )
    from gideon.interfaces.dashboard.handlers.tools import api_tool_invoke, api_tools_list

    app = web.Application()
    app.router.add_get("/api/tools", api_tools_list)
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    client = TestClient(TestServer(app))
    try:
        await client.start_server()
        response = await client.get("/api/tools")
        tools = (await response.json())["tools"]
        canonical = "mcp/try-demo/echo"
        assert canonical in {tool["name"] for tool in tools}

        response = await client.post(
            "/api/tools/invoke",
            headers={"X-Session-Key": "mcp04-try-session"},
            json={"tool": canonical, "arguments": {"value": "same surface"}},
        )
        result = await response.json()
        assert response.status == 200
        assert result["ok"] is True
        assert "same surface" in result["output"]

        response = await client.post(
            "/api/tools/invoke",
            json={
                "tool": canonical,
                "arguments": {"value": "wrong provider"},
                "provider": "another-server",
            },
        )
        assert response.status == 404
    finally:
        await client.close()
        config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        if mcp_client._registry is not None:
            await mcp_client._registry.shutdown_all()
        mcp_client._registry = None
        from gideon.integrations.tool_providers.registry import list_providers

        list_providers()
