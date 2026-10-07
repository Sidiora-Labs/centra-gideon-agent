import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.automation.workflows.bindings import BindingContext
from gideon.automation.workflows.engine import dispatch_action
from gideon.automation.workflows.models import Node, NodeKind
from gideon.integrations.action_providers.net_fetch_provider import (
    NetFetchActionProvider,
)
from gideon.integrations.mcp_client import McpServerConn, _remote_http_client_factory
from gideon.integrations.mcp_core import (
    reset_current_session_key,
    set_current_session_key,
)
from gideon.sdk.net import egress_refusal
from gideon.security.guardrails.ceiling import reset_ceiling
from gideon.security.net.client import EgressBlocked, fetch
from gideon.security.net.guard import evaluate
from gideon.security.net.policy import (
    LOOPBACK_INTERNAL,
    MCP_SERVER,
    STRICT,
    EgressPolicy,
    egress_held_to,
    egress_policy_for,
    egress_policy_for_profile,
    run_of_this_call,
)


@pytest.fixture
def network_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    ceiling = tmp_path / "ceiling.json"
    monkeypatch.setenv("GIDEON_CEILING_FILE", str(ceiling))
    reset_ceiling()
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "security": {"egress": {"allow_hosts": ["localhost", "127.0.0.1"]}},
            }
        )
    )
    yield tmp_path, ceiling
    reset_ceiling()


def ceiling_tier(path, tier):
    path.write_text(json.dumps({"version": 1, "scopes": {"egress": {"value": tier}}}))
    reset_ceiling()


@pytest.mark.asyncio
async def test_guarded_redirect_keeps_run_list_before_second_request(network_home):
    home, ceiling = network_home
    (home / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_hosts": ["localhost"]}}})
    )
    ceiling_tier(ceiling, "listed")
    seen = []
    app = web.Application()

    async def entry(request):
        seen.append(request.path)
        raise web.HTTPFound(f"http://127.0.0.1:{request.url.port}/destination")

    async def destination(request):
        seen.append(request.path)
        return web.Response(text="destination")

    app.router.add_get("/entry", entry)
    app.router.add_get("/destination", destination)
    async with TestServer(app) as server:
        url = f"http://localhost:{server.port}/entry"
        with egress_held_to("unattended:trigger:local"):
            with pytest.raises(EgressBlocked) as caught:
                await fetch(url)
        assert caught.value.decision.category == "not_listed"
        assert seen == ["/entry"]


@pytest.mark.asyncio
async def test_workflow_fetch_uses_verified_dispatch_run_ceiling(network_home):
    _, ceiling = network_home
    ceiling_tier(ceiling, "off")
    seen = []
    app = web.Application()

    async def destination(request):
        seen.append(request.path)
        return web.Response(text="reached")

    app.router.add_get("/destination", destination)
    async with TestServer(app) as server:
        provider = NetFetchActionProvider()
        node = Node(
            kind=NodeKind.ACTION,
            config={
                "provider": "net-fetch",
                "with": {"url": str(server.make_url("/destination"))},
                "payload": {"run_id": "untrusted-id"},
            },
        )
        result = await dispatch_action(
            node,
            BindingContext(),
            get_provider=lambda name: provider,
            run_id="verified-run",
        )
        assert result.state.value == "failed"
        assert not seen
        assert "egress is off" in str(result.failure)


def test_held_context_is_egress_only_and_inner_session_wins(network_home):
    outer = set_current_session_key("chat:outer")
    try:
        with egress_held_to("unattended:trigger:one"):
            assert run_of_this_call() == "unattended:trigger:one"
            inner = set_current_session_key("chat:inner")
            try:
                assert run_of_this_call() == "chat:inner"
            finally:
                reset_current_session_key(inner)
            assert run_of_this_call() == "unattended:trigger:one"
        assert run_of_this_call() == "chat:outer"
    finally:
        reset_current_session_key(outer)


def test_exclusive_surface_not_widened_by_registry_and_internal_loopback_preserved(
    network_home,
):
    _, ceiling = network_home
    base = EgressPolicy(allow_only=True, allow_hosts=("localhost",), max_bytes=100)
    narrowed = egress_policy_for_profile(base, "registry")
    assert narrowed.allow_hosts == ("localhost",)
    assert narrowed.max_bytes == 100
    ceiling_tier(ceiling, "off")
    with egress_held_to("unattended:trigger:one"):
        assert evaluate("http://127.0.0.1:1/", LOOPBACK_INTERNAL).allow
        decision = evaluate("http://127.0.0.1:1/", MCP_SERVER)
    assert decision.category == "egress_off"
    assert "Allowed hosts" not in egress_refusal(decision.url, decision)


@pytest.mark.asyncio
async def test_remote_transport_local_endpoint_and_cross_origin_redirect(network_home):
    hits = []
    app = web.Application()

    async def entry(request):
        hits.append(request.path)
        raise web.HTTPFound(f"http://localhost:{request.url.port}/destination")

    async def destination(request):
        hits.append(request.path)
        return web.Response(text="reached")

    app.router.add_get("/entry", entry)
    app.router.add_get("/destination", destination)
    async with TestServer(app) as server:
        endpoint = str(server.make_url("/entry"))
        async with _remote_http_client_factory(endpoint=endpoint) as client:
            with pytest.raises(ValueError, match="cross-origin"):
                await client.get(endpoint, follow_redirects=True)
        assert hits == ["/entry"]
        endpoint = str(server.make_url("/destination"))
        async with _remote_http_client_factory(endpoint=endpoint) as client:
            response = await client.get(endpoint)
            assert response.text == "reached"
        assert hits == ["/entry", "/destination"]


@asynccontextmanager
async def local_mcp_server():
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    calls = []
    server = FastMCP("local-egress-contract", stateless_http=True, json_response=True)

    @server.tool()
    def ping() -> str:
        calls.append("ping")
        return "reached"

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    runtime = uvicorn.Server(
        uvicorn.Config(server.streamable_http_app(), log_level="error")
    )
    task = asyncio.create_task(runtime.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not runtime.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/mcp", calls
    finally:
        runtime.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


@pytest.mark.asyncio
async def test_open_mcp_call_refused_without_failed_start_or_network(network_home):
    _, ceiling = network_home
    async with local_mcp_server() as (endpoint, calls):
        from gideon.extensions.providers import mcp_instances
        from gideon.security import mcp_grants
        from gideon.security.approval_answer import Principal

        spec = {"url": endpoint, "transport": "http"}
        mcp_instances._save({"mcpServers": {"local-contract": spec}})
        mcp_grants.give(
            {"name": "local-contract", "source": "mcp.json", **spec},
            Principal("owner", "local-contract-test"),
        )
        connection = McpServerConn("local-contract", spec)
        try:
            assert await connection.ensure_started(), connection.error
            actor = connection._task
            with egress_held_to("chat:allowed"):
                ok, text = await connection.call_tool("ping", {})
            assert ok and "reached" in text
            assert calls == ["ping"]
            ceiling_tier(ceiling, "off")
            with egress_held_to("unattended:trigger:one"):
                ok, text = await connection.call_tool("ping", {})
            assert not ok and "was not reached" in text and "egress is off" in text
            assert calls == ["ping"]
            assert connection._task is actor
            assert connection.error == ""
            assert connection._consecutive_failures == 0
        finally:
            await connection.shutdown()
