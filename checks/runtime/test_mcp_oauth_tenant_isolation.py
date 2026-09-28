from __future__ import annotations

import pytest

from gideon.integrations.mcp_oauth import (
    OAuthCallbackError,
    consume_callback_state,
    issue_callback_state,
)
from gideon.security.approval_answer import Principal, OWNER


def test_oauth_callback_state_is_bound_to_owner_tenant_server_resource_and_revision():
    owner = Principal(OWNER, "alice", "tenant-a")
    state = issue_callback_state(
        owner,
        server="search",
        resource="https://mcp.example/resource",
        revision="definition-r1",
        sdk_state="sdk-state-random",
    )

    with pytest.raises(OAuthCallbackError):
        consume_callback_state(
            state,
            Principal(OWNER, "mallory", "tenant-a"),
            server="search",
            resource="https://mcp.example/resource",
            revision="definition-r1",
        )
    with pytest.raises(OAuthCallbackError):
        consume_callback_state(
            state,
            owner,
            server="search",
            resource="https://other.example/resource",
            revision="definition-r1",
        )
    with pytest.raises(OAuthCallbackError):
        consume_callback_state(
            state,
            owner,
            server="search",
            resource="https://mcp.example/resource",
            revision="definition-r2",
        )

    callback = consume_callback_state(
        state,
        owner,
        server="search",
        resource="https://mcp.example/resource",
        revision="definition-r1",
    )
    assert callback.sdk_state == "sdk-state-random"
    with pytest.raises(OAuthCallbackError):
        consume_callback_state(
            state,
            owner,
            server="search",
            resource="https://mcp.example/resource",
            revision="definition-r1",
        )


def test_oauth_callback_state_rejects_tampering_and_non_owner_principals():
    owner = Principal(OWNER, "alice", "tenant-a")
    state = issue_callback_state(
        owner,
        server="search",
        resource="https://mcp.example/resource",
        revision="definition-r1",
        sdk_state="sdk-state-random",
    )
    nonce, signature = state.split(".", 1)
    tampered = nonce + "." + ("A" if signature[0] != "A" else "B") + signature[1:]

    with pytest.raises(OAuthCallbackError):
        consume_callback_state(
            tampered,
            owner,
            server="search",
            resource="https://mcp.example/resource",
            revision="definition-r1",
        )
    with pytest.raises(PermissionError):
        consume_callback_state(
            state,
            Principal("app", "search", "tenant-a"),
            server="search",
            resource="https://mcp.example/resource",
            revision="definition-r1",
        )


def test_oauth_endpoint_http_metadata_is_refused_by_production_transport(tmp_path, monkeypatch):
    import asyncio

    from mcp.client.auth.oauth2 import OAuthContext
    from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata

    from gideon.integrations.mcp_client import _remote_http_client_factory
    from gideon.integrations.mcp_oauth import McpOAuthStorage

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    resource = "https://mcp.example.test/rpc"
    server = "transport-check"

    async def denied_before_network():
        for field in ("token_endpoint", "registration_endpoint", "revocation_endpoint", "introspection_endpoint"):
            endpoints = {
                "issuer": "https://issuer.example.test",
                "authorization_endpoint": "https://issuer.example.test/authorize",
                "token_endpoint": "https://issuer.example.test/token",
                "registration_endpoint": None,
                "revocation_endpoint": None,
                "introspection_endpoint": None,
            }
            endpoints[field] = f"http://issuer.example.test/{field}"
            metadata = OAuthMetadata(**endpoints)
            context = OAuthContext(
                server_url=resource,
                client_metadata=OAuthClientMetadata(
                    redirect_uris=["https://dashboard.example.test/api/mcp/oauth/callback"]
                ),
                storage=McpOAuthStorage(server, resource),
                redirect_handler=None,
                callback_handler=None,
                oauth_metadata=metadata,
                auth_server_url="https://issuer.example.test",
            )
            client = _remote_http_client_factory(
                endpoint=resource,
                oauth_context=context,
            )
            try:
                with pytest.raises(ValueError, match="OAuth endpoints require HTTPS"):
                    await client.get(f"http://issuer.example.test/{field}")
            finally:
                await client.aclose()

    asyncio.run(denied_before_network())


def test_resource_metadata_challenge_accepts_parameter_after_scheme():
    from gideon.integrations.mcp_client import _resource_metadata_from_challenge

    assert _resource_metadata_from_challenge(
        'Bearer resource_metadata="https://metadata.example/path"'
    ) == "https://metadata.example/path"
    assert _resource_metadata_from_challenge(
        'Bearer realm="mcp", resource_metadata="https://metadata.example/path"'
    ) == "https://metadata.example/path"
    assert _resource_metadata_from_challenge(
        'Bearer resource_metadata="http://metadata.example/path"'
    ) == "http://metadata.example/path"


def test_rejected_oauth_issuer_cleans_exact_callback_future_and_task():
    import asyncio

    import gideon.integrations.mcp_oauth as mcp_oauth

    owner = Principal(OWNER, "alice", "tenant-a")

    async def reject_and_check_cleanup():
        state = issue_callback_state(
            owner,
            server="search",
            resource="https://mcp.example/resource",
            revision="definition-r1",
            sdk_state="sdk-state-random",
            issuer="https://issuer.example",
        )
        nonce = state.split(".", 1)[0]
        callback = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(asyncio.Event().wait())
        mcp_oauth._CALLBACK_FUTURES[nonce] = callback
        mcp_oauth._AUTHORIZATION_TASKS[nonce] = task

        with pytest.raises(OAuthCallbackError, match="issuer does not match"):
            await mcp_oauth.complete_dashboard_callback(
                state,
                "authorization-code",
                owner,
                server="search",
                resource="https://mcp.example/resource",
                revision="definition-r1",
                issuer="https://other-issuer.example",
            )

        assert nonce not in mcp_oauth._PENDING_CALLBACKS
        assert nonce not in mcp_oauth._CALLBACK_FUTURES
        assert nonce not in mcp_oauth._AUTHORIZATION_TASKS
        assert callback.cancelled()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()

    asyncio.run(reject_and_check_cleanup())
