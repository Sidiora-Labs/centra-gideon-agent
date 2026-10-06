import io
import urllib.error

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.script_worker import GatewayChannel
from gideon.integrations.mcp_core import (
    InternalSecretUnavailable,
    _internal_secret,
    _refused,
)
from gideon.interfaces.dashboard.token_auth import (
    INTERNAL_ROUTES,
    MIXED_INTERNAL_ROUTES,
    InternalRoute,
    token_auth_middleware,
)


def test_internal_operation_templates_are_whole_method_and_path():
    route = InternalRoute.parse("GET /api/spawn/{agent_id}")
    assert route.admits("GET", "/api/spawn/one")
    assert not route.admits("POST", "/api/spawn/one")
    assert not route.admits("GET", "/api/spawn/one/control")
    assert not route.admits("GET", "/api/spawn/")
    assert not route.admits("GET", "/api/spawn/one/{two}")
    assert InternalRoute.parse("DELETE /api/memory/approval-rules/{key:.+}").admits(
        "DELETE", "/api/memory/approval-rules/a/b"
    )
    assert not any(
        InternalRoute.parse(entry).admits("DELETE", "/api/mcp/servers/local")
        for entry in INTERNAL_ROUTES | MIXED_INTERNAL_ROUTES
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bypass", [None, "GIDEON_DEV_NO_AUTH", "GIDEON_BYPASS_LOCAL_NETWORKS"]
)
async def test_secret_cannot_authorize_other_method_or_descendant(monkeypatch, bypass):
    if bypass:
        monkeypatch.setenv(bypass, "1")
    app = web.Application(
        middlewares=[
            token_auth_middleware(
                mixed_internal_routes=frozenset({"GET /api/spawn/{agent_id}"}),
                internal_secret="issued-secret",
            )
        ]
    )

    async def ok(request):
        return web.json_response({"ok": True})

    app.router.add_route("*", "/api/{tail:.*}", ok)
    async with TestClient(TestServer(app)) as client:
        for method, path, expected in [
            ("GET", "/api/spawn/one", 200),
            ("DELETE", "/api/spawn/one", 403),
            ("GET", "/api/spawn/one/control", 403),
            ("GET", "/api/mcp/servers/local", 403),
        ]:
            response = await client.request(
                method, path, headers={"X-Internal-Secret": "issued-secret"}
            )
            assert response.status == expected
            if expected == 403:
                assert (await response.json())["error"][
                    "code"
                ] == "internal_route_refused"
        response = await client.get(
            "/api/spawn/one", headers={"X-Internal-Secret": "wrong"}
        )
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "internal_secret_invalid"


def test_internal_secret_uses_explicit_home_without_creating_it(tmp_path, monkeypatch):
    home = tmp_path / "not-created"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    with pytest.raises(InternalSecretUnavailable):
        _internal_secret()
    assert not home.exists()
    home.mkdir()
    (home / ".local_secret").write_text("issued-secret\n")
    assert _internal_secret() == "issued-secret"
    (home / ".local_secret").write_text("")
    with pytest.raises(InternalSecretUnavailable):
        _internal_secret()


def test_callback_keeps_wire_refusal_and_missing_script_credential():
    error = urllib.error.HTTPError(
        "http://127.0.0.1/",
        403,
        "Forbidden",
        {},
        io.BytesIO(
            b'{"error":{"code":"internal_route_refused","message":"Operation refused."}}'
        ),
    )
    result = _refused(error)
    assert result["error"] == "Operation refused."
    assert result["error_detail"]["code"] == "internal_route_refused"
    channel = GatewayChannel({"port": 0, "secret_unavailable": "No credential."})
    result = channel.send("/api/send-message", {"text": "hello"})
    assert result["status"] == 0
    assert result["error"]["code"] == "internal_secret_unavailable"
