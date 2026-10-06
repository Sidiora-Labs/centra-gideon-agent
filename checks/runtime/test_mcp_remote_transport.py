"""Remote MCP clients use normalized transports and the shared egress guard."""

from __future__ import annotations

import pytest


def test_transport_normalization_accepts_only_declared_remote_modes():
    from gideon.integrations.mcp_client import normalize_transport

    assert (
        normalize_transport({"url": "https://mcp.example.test", "transport": "sse"})
        == "sse"
    )
    assert (
        normalize_transport(
            {"url": "https://mcp.example.test", "transport": "streamable_http"}
        )
        == "streamable-http"
    )
    assert normalize_transport({"command": "node", "transport": "stdio"}) == "stdio"
    with pytest.raises(ValueError):
        normalize_transport({"url": "https://mcp.example.test", "command": "node"})
    with pytest.raises(ValueError):
        normalize_transport({"url": "https://mcp.example.test", "transport": "unknown"})


@pytest.mark.asyncio
async def test_guarded_mcp_http_client_refuses_private_and_cross_origin_requests():
    import httpx

    from gideon.integrations.mcp_client import _remote_http_client_factory
    from gideon.security.net.client import EgressBlocked

    async with _remote_http_client_factory(
        endpoint="http://127.0.0.1:43127/mcp"
    ) as private:
        with pytest.raises(EgressBlocked):
            await private.get("http://127.0.0.1:43127/mcp")

    async with _remote_http_client_factory(
        endpoint="https://mcp.example.test/mcp"
    ) as public:
        with pytest.raises(ValueError, match="cross-origin"):
            await public.get("https://other.example.test/message")
        with pytest.raises(ValueError, match="userinfo"):
            await public.get("https://user:password@mcp.example.test/mcp")
