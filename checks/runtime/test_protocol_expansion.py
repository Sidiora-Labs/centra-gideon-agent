"""Real wire checks for ACP stdio and MCP delegated resource/prompt calls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.external_access import ExternalAccessConfig
from gideon.core.config.external_access import ExternalAccessSurfaceConfig
from gideon.core.config.loader import AppConfig
from gideon.integrations.inbound import a2a, auth
from gideon.automation.workflows import defs as workflow_defs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.integrations.mcp_client import McpServerConn
from gideon.integrations.mcp_oauth import McpOAuthStorage


@pytest.mark.asyncio
async def test_acp_cli_initializes_and_validates_sessions(tmp_path):
    env = {**os.environ, "GIDEON_HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join(sys.path)}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "gideon", "acp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}\n')
        await process.stdin.drain()
        initialized = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert initialized["id"] == 1
        assert initialized["result"]["agentInfo"]["name"] == "Gideon"
        process.stdin.write(b'{"jsonrpc":"2.0","id":2,"method":"session/prompt","params":{"sessionId":"absent","prompt":[{"type":"text","text":"hello"}]}}\n')
        await process.stdin.drain()
        missing = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert missing["error"]["code"] == -32001
    finally:
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 20)


@pytest.mark.asyncio
async def test_mcp_actor_reads_real_server_resources_and_prompts():
    server_source = '''from mcp.server.fastmcp import FastMCP
server = FastMCP("Gideon protocol check")
@server.resource("sample://hello")
def hello() -> str:
    return "resource from real MCP server"
@server.prompt()
def welcome(name: str) -> str:
    return "Welcome " + name
server.run()
'''
    connection = McpServerConn("protocol-check", {"command": sys.executable, "args": ["-c", server_source]})
    try:
        resources = await connection.protocol_call("resources/list")
        assert any(item["uri"] == "sample://hello" for item in resources["resources"])
        read = await connection.protocol_call("resources/read", uri="sample://hello")
        assert "resource from real MCP server" in json.dumps(read)
        prompts = await connection.protocol_call("prompts/list")
        assert any(item["name"] == "welcome" for item in prompts["prompts"])
        prompt = await connection.protocol_call("prompts/get", name="welcome", arguments={"name": "Sir"})
        assert "Welcome Sir" in json.dumps(prompt)
    finally:
        await connection.shutdown()


@pytest.mark.asyncio
async def test_mcp_oauth_tokens_stay_in_private_server_store(tmp_path, monkeypatch):
    from mcp.shared.auth import OAuthToken

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    storage = McpOAuthStorage("configured-server", "https://example.test/mcp")
    await storage.set_tokens(OAuthToken(access_token="fixture-token", token_type="Bearer"))
    assert (await storage.get_tokens()).access_token == "fixture-token"
    assert storage.path.stat().st_mode & 0o077 == 0
    assert storage.path.parent.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_a2a_jsonrpc_uses_authenticated_task_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    config = AppConfig()
    config.external_access = ExternalAccessConfig(enabled=True, a2a=ExternalAccessSurfaceConfig(enabled=True))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: config))
    token = "p" * 48
    monkeypatch.setenv(auth.token_env_key("a2a"), token)
    app = web.Application()
    a2a.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        frame = {"jsonrpc": "2.0", "id": 4, "method": "tasks/get", "params": {"id": "foreign-run"}}
        denied = await client.post("/a2a", json=frame)
        assert denied.status == 401
        response = await client.post("/a2a", json=frame, headers={"Authorization": f"Bearer {token}"})
        assert response.status == 200
        assert (await response.json())["error"]["code"] == -32001
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a2a_jsonrpc_start_list_subscribe_and_cancel_real_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    config = AppConfig()
    config.external_access = ExternalAccessConfig(enabled=True, a2a=ExternalAccessSurfaceConfig(enabled=True))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: config))
    token = "q" * 48
    monkeypatch.setenv(auth.token_env_key("a2a"), token)
    provider = NativeWorkflowDefProvider()
    workflow_defs.register_provider(provider)
    await provider.save_def(
        name="protocol-wait",
        root={"id": "waiting", "kind": "wait", "config": {"duration_secs": 300}},
        metadata={"a2a_published": True, "summary": "Wait for cancellation"},
    )
    watchdog = WorkflowWatchdog()
    app = web.Application()
    app["state"] = SimpleNamespace(workflows=watchdog)
    a2a.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"Authorization": f"Bearer {token}"}
    async def rpc(method, params, request_id):
        response = await client.post("/a2a", json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, headers=headers)
        assert response.status == 200
        return await response.json()
    try:
        sent = await rpc("message/send", {"message": {"messageId": "protocol-test-1", "parts": [{"kind": "text", "text": "wait"}], "metadata": {"skillId": "protocol-wait"}}}, 1)
        task_id = sent["result"]["id"]
        assert (await rpc("tasks/get", {"id": task_id}, 2))["result"]["id"] == task_id
        listed = await rpc("tasks/list", {"limit": 20}, 3)
        assert task_id in {task["id"] for task in listed["result"]["tasks"]}
        subscription = await client.post("/a2a", json={"jsonrpc": "2.0", "id": 4, "method": "tasks/resubscribe", "params": {"id": task_id}}, headers=headers)
        first = await asyncio.wait_for(subscription.content.readline(), 10)
        assert task_id in first.decode()
        subscription.close()
        cancelled = await rpc("tasks/cancel", {"id": task_id}, 5)
        assert cancelled["result"]["id"] == task_id
        controller = watchdog.controller(task_id)
        assert controller is not None
        await controller.wait_for_terminal(timeout=10)
        current = await rpc("tasks/get", {"id": task_id}, 6)
        assert current["result"]["status"]["state"] == "canceled"
    finally:
        await client.close()
        await watchdog.stop()
        workflow_defs.unregister_provider(provider.name)
