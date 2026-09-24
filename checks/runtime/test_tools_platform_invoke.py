import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.agents.native.builtin_tools import create_platform_tools_provider
from gideon.http_errors import HTTP_ERROR_CODES
from gideon.integrations.tool_providers.registry import get_provider
from gideon.interfaces.dashboard.handlers.tools import api_tool_invoke


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", [None, "gideon-filesystem"])
@pytest.mark.parametrize("tool", ["read_file", "bash"])
async def test_platform_http_invocation_resolves_each_workspace(
    monkeypatch, tmp_path, provider_name, tool
):
    workspaces = [tmp_path / "first", tmp_path / "second"]
    for directory in workspaces:
        directory.mkdir()
        (directory / "value.txt").write_text(directory.name)
    process_directory = tmp_path / "process"
    process_directory.mkdir()
    (process_directory / "value.txt").write_text("wrong workspace")
    monkeypatch.chdir(process_directory)
    original_provider = get_provider("gideon-filesystem")
    app = web.Application()
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    async with TestClient(TestServer(app)) as client:
        for directory in [*workspaces, workspaces[0]]:
            monkeypatch.setenv("GIDEON_WORKSPACE", str(directory))
            body = {
                "tool": tool,
                "arguments": (
                    {"path": "value.txt"}
                    if tool == "read_file"
                    else {"command": "pwd; cat value.txt"}
                ),
            }
            if provider_name is not None:
                body["provider"] = provider_name
            response = await client.post("/api/tools/invoke", json=body)
            result = await response.json()
            assert response.status == 200, result
            assert result["ok"], result
            assert result["output"].strip().endswith(directory.name)
            assert "wrong workspace" not in result["output"]
            if tool == "bash":
                assert str(directory) in result["output"]
    assert get_provider("gideon-filesystem") is original_provider


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", [None, "gideon-filesystem"])
@pytest.mark.parametrize("tool", ["read_file", "bash"])
async def test_unresolvable_workspace_returns_503_without_running_tool(
    monkeypatch, tmp_path, provider_name, tool
):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("unchanged")
    monkeypatch.setenv("GIDEON_WORKSPACE", str(blocked / "workspace"))
    marker = tmp_path / "executed"
    body = {
        "tool": tool,
        "arguments": (
            {"path": "value.txt"}
            if tool == "read_file"
            else {"command": f"touch {marker}"}
        ),
    }
    if provider_name is not None:
        body["provider"] = provider_name
    app = web.Application()
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/tools/invoke", json=body)
        result = await response.json()
    assert response.status == 503
    assert result == {
        "ok": False,
        "error": {
            "code": "workspace_unresolved",
            "message": HTTP_ERROR_CODES["workspace_unresolved"],
        },
    }
    assert not marker.exists()
    assert blocked.read_text() == "unchanged"


@pytest.mark.asyncio
async def test_factory_cwd_isolation_with_concurrent_real_tools(tmp_path):
    async def operate(directory):
        directory.mkdir()
        (directory / "value.txt").write_text(directory.name)
        provider = create_platform_tools_provider(cwd=directory)
        result = await provider.invoke("read_file", {"path": "value.txt"})
        assert result.success and result.output == directory.name
        result = await provider.invoke("bash", {"command": "pwd; cat value.txt"})
        assert result.success, result.error
        assert str(directory) in result.output
        assert result.output.strip().endswith(directory.name)

    await asyncio.gather(operate(tmp_path / "left"), operate(tmp_path / "right"))


@pytest.mark.asyncio
async def test_unknown_platform_tool_stays_404_when_workspace_is_unavailable(
    monkeypatch, tmp_path
):
    blocked = tmp_path / "file"
    blocked.write_text("unchanged")
    monkeypatch.setenv("GIDEON_WORKSPACE", str(blocked / "workspace"))
    app = web.Application()
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    async with TestClient(TestServer(app)) as client:
        for provider in ("gideon-filesystem", "unknown-provider"):
            response = await client.post(
                "/api/tools/invoke",
                json={"tool": "unknown-tool", "provider": provider},
            )
            assert response.status == 404
