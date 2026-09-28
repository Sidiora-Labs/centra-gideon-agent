"""Owner sessions may travel in a Bearer header without becoming app credentials."""

from __future__ import annotations

import json

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import session_store, token_auth

PORT = 10000
COOKIE = f"gideon_token_{PORT}"


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    from gideon.core.config import loader

    isolated_home = tmp_path / ".gideon"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(isolated_home))
    assert loader.config_dir() == isolated_home
    assert session_store.config_dir() == isolated_home
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"gideon-owner-bearer-contract-key")
    token_auth.revoke_all_sessions()
    yield
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def identity(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "user": request["user"],
            "app": request.get("app", ""),
            "session_nonce": request["session_nonce"],
        }
    )


async def ws_identity(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json(
        {"user": request["user"], "session_nonce": request["session_nonce"]}
    )
    await ws.close()
    return ws


async def make_client() -> TestClient:
    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(
                port=PORT,
                internal_paths=frozenset({"/api/internal-probe"}),
                mixed_internal_paths=frozenset({"/api/mixed-probe"}),
            )
        ]
    )
    for path in ("/api/probe", "/api/internal-probe", "/api/mixed-probe"):
        app.router.add_get(path, identity)
    app.router.add_get("/api/ws", ws_identity)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def assert_bearer_refused(response, token: str) -> None:
    assert response.status == 403, await response.text()
    body = await response.text()
    assert json.loads(body)["error"] == token_auth.ERR_BEARER_INVALID
    assert token not in body
    assert "Set-Cookie" not in response.headers


@pytest.mark.asyncio
async def test_owner_bearer_authenticates_http_internal_and_websocket_without_cookie():
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await make_client()
    try:
        for path in ("/api/probe", "/api/internal-probe", "/api/mixed-probe"):
            response = await client.get(path, headers=bearer(token))
            assert response.status == 200, await response.text()
            assert (await response.json())["user"] == "owner"
            assert "Set-Cookie" not in response.headers

        ws = await client.ws_connect("/api/ws", headers=bearer(token))
        message = await ws.receive()
        assert message.type is WSMsgType.TEXT
        assert json.loads(message.data) == {
            "user": "owner",
            "session_nonce": token_auth.token_nonce(token),
        }
        await ws.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_query_exchange_sets_cookie_and_same_header_keeps_exchange():
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await make_client()
    try:
        response = await client.get(
            "/api/probe", params={"token": token}, headers=bearer(token)
        )
        assert response.status == 200
        assert response.cookies[COOKIE].value == token
        assert (await response.json())["user"] == "owner"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_conflicting_owner_query_and_header_are_refused_without_echo():
    query_token = token_auth.generate_token("owner", ttl_seconds=300)
    header_token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await make_client()
    try:
        response = await client.get(
            "/api/probe", params={"token": query_token}, headers=bearer(header_token)
        )
        assert response.status == 403
        body = await response.text()
        assert json.loads(body)["error"] == token_auth.ERR_CREDENTIAL_CONFLICT
        assert query_token not in body and header_token not in body
        assert "Set-Cookie" not in response.headers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_app_bearer_only_narrows_an_owner_session():
    app_token = token_auth.generate_token("owner", ttl_seconds=300, app="notes")
    owner = token_auth.generate_token("owner", ttl_seconds=300)
    client = await make_client()
    try:
        await assert_bearer_refused(
            await client.get("/api/probe", headers=bearer(app_token)), app_token
        )

        narrowed = await client.get(
            "/api/probe", cookies={COOKIE: owner}, headers=bearer(app_token)
        )
        assert narrowed.status == 200
        assert (await narrowed.json())["app"] == "notes"
        assert (await narrowed.json())["session_nonce"] == token_auth.token_nonce(owner)

        app_ws = await client.get(
            "/api/probe", params={"app_token": app_token}, headers=bearer(owner)
        )
        assert app_ws.status == 200
        assert (await app_ws.json())["app"] == "notes"
    finally:
        await client.close()


@pytest.mark.parametrize(
    "token", ["forged-token", "bad.signatureé", "Bearer-with-whitespace token"]
)
@pytest.mark.asyncio
async def test_malformed_and_forged_bearers_have_one_value_free_error(token: str):
    client = await make_client()
    try:
        await assert_bearer_refused(
            await client.get("/api/probe", headers=bearer(token)), token
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bearer_signed_by_another_gateway_is_refused():
    token_auth.use_ephemeral_secret(b"another-gateway-signing-key")
    foreign_token = token_auth.generate_token("owner", ttl_seconds=300)
    token_auth.use_ephemeral_secret(b"gideon-owner-bearer-contract-key")
    client = await make_client()
    try:
        await assert_bearer_refused(
            await client.get("/api/probe", headers=bearer(foreign_token)),
            foreign_token,
        )
    finally:
        await client.close()
