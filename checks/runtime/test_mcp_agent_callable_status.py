"""MCP readiness follows tools that the production agent catalog can call."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_SERVER = textwrap.dedent("""
    from typing import Any
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("catalog-fixture")

    @mcp.tool()
    def greet(name: str) -> str:
        \"\"\"Return a greeting.\"\"\"
        return f"hello {name}"

    @mcp.tool()
    def map_only(values: dict[str, Any]) -> str:
        \"\"\"Accept an open-ended map.\"\"\"
        return str(values)

    if __name__ == "__main__":
        mcp.run()
    """)


async def _reset_client_registry() -> None:
    from gideon.integrations import mcp_client

    prior = mcp_client._registry
    if prior is not None:
        await prior.shutdown_all()
    mcp_client._registry = None


def _configure_server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    from gideon.core.config.loader import config_dir

    script = tmp_path / "mcp_fixture.py"
    script.write_text(_SERVER, encoding="utf-8")
    spec = {"command": sys.executable, "args": [str(script)]}
    config = config_dir() / "mcp.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"mcpServers": {"catalog-demo": spec}}), encoding="utf-8"
    )
    from gideon.integrations.mcp_discovery import McpServerInfo
    from gideon.security import mcp_grants
    from gideon.security.approval_answer import OWNER, Principal

    mcp_grants.give(
        McpServerInfo(
            name="catalog-demo",
            command=sys.executable,
            args=[str(script)],
            source="mcp.json",
        ),
        Principal(OWNER, "mcp04-test-owner"),
    )
    return config, spec


@pytest.mark.asyncio
async def test_all_mcp_status_routes_use_the_portable_agent_catalog(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))
    await _reset_client_registry()
    config, _spec = _configure_server(tmp_path, monkeypatch)
    from gideon.interfaces.dashboard.handlers import mcp as handlers

    handlers._mcp_probe_cache.clear()
    handlers._mcp_probe_ts = 0
    app = web.Application()
    app.router.add_get("/api/mcp", handlers.api_mcp_servers)
    app.router.add_get("/api/mcp/probe", handlers.api_mcp_probe_cached)
    app.router.add_post("/api/mcp/probe", handlers.api_mcp_probe)
    app.router.add_post("/api/mcp/probe/{name}", handlers.api_mcp_probe_one)
    client = TestClient(TestServer(app))
    try:
        await client.start_server()
        response = await client.post("/api/mcp/probe")
        assert response.status == 200
        row = (await response.json())[0]
        assert row["name"] == "catalog-demo"
        assert row["status"] == "ready"
        assert row["healthStatus"] == "ok"
        assert row["agentCallable"] is True
        assert row["agentCallableToolCount"] == 1
        assert not row["unservedReason"]

        response = await client.get("/api/mcp")
        row = next(
            item for item in await response.json() if item["name"] == "catalog-demo"
        )
        assert row["status"] == "ready"
        assert row["agentCallable"] is True

        response = await client.get("/api/mcp/probe")
        row = next(
            item for item in await response.json() if item["name"] == "catalog-demo"
        )
        assert row["status"] == "ready"

        response = await client.post("/api/mcp/probe/catalog-demo")
        row = await response.json()
        assert row["status"] == "ready"
        assert row["healthStatus"] == "ok"

        document = json.loads(config.read_text(encoding="utf-8"))
        document["mcpServers"]["catalog-demo"]["disabledTools"] = ["greet"]
        config.write_text(json.dumps(document), encoding="utf-8")
        response = await client.get("/api/mcp")
        row = next(
            item for item in await response.json() if item["name"] == "catalog-demo"
        )
        assert row["status"] == "unserved"
        assert row["healthStatus"] in {"ok", "outdated", "unknown"}
        assert row["agentCallable"] is False
        assert row["agentCallableToolCount"] == 0
        assert row["unservedReason"]
    finally:
        await client.close()
        config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        await _reset_client_registry()
        from gideon.integrations.tool_providers.registry import list_providers

        list_providers()
