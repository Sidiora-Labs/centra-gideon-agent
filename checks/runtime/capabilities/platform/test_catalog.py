import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps.app_events import PLATFORM_EVENTS
from gideon.interfaces.dashboard.handlers.capabilities_platform import (
    api_catalog,
    register,
)
from gideon.interfaces.dashboard.handlers.prompts import api_prompt_syntax
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    revoke_all_sessions,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.workspace.capabilities.platform.catalog import CATALOG_PATH, build_catalog


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    revoke_all_sessions()
    yield
    revoke_all_sessions()


def application(dynamic=True):
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    app.router.add_get("/api/prompts/syntax", api_prompt_syntax, name="prompt-syntax")
    if dynamic:
        app.router.add_get(
            "/api/explorer/{record_id}", api_catalog, name="record-catalog"
        )
        app.router.add_post("/api/explorer/{record_id}", api_catalog)
    return app


@pytest.mark.asyncio
async def test_authentication_and_registered_routes():
    app = application()
    async with TestClient(TestServer(app)) as client:
        denied = await client.get(CATALOG_PATH)
        assert denied.status == 403
        invalid = await client.get(CATALOG_PATH, params={"token": "invalid"})
        assert invalid.status == 403
        token = generate_token("catalog-owner")
        result = await client.get(CATALOG_PATH, params={"token": token})
        assert result.status == 200
        assert result.headers["Cache-Control"] == "no-store"
        catalog = await result.json()
        assert catalog["version"] == 1
        expected = {
            (route.method, route.resource.canonical) for route in app.router.routes()
        }
        actual = {(route["method"], route["path"]) for route in catalog["routes"]}
        assert actual == expected
        assert ("GET", "/api/explorer/{record_id}") in actual
        assert ("POST", "/api/explorer/{record_id}") in actual
        assert all(
            route["schema"] == {"status": "unknown"} for route in catalog["routes"]
        )
        assert token not in json.dumps(catalog)
        followup = await client.get(CATALOG_PATH)
        assert followup.status == 200
        assert await followup.json() == catalog


@pytest.mark.asyncio
async def test_app_scoped_identity_cannot_enumerate_dashboard():
    async with TestClient(TestServer(application())) as client:
        token = generate_token("catalog-owner", app="restricted-app")
        response = await client.get(CATALOG_PATH, params={"token": token})
        assert response.status == 403
        assert "Dashboard authentication required" in await response.text()


@pytest.mark.asyncio
async def test_handler_fails_closed_without_auth_middleware():
    app = web.Application()
    register(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(CATALOG_PATH)
        assert response.status == 403
        assert "Dashboard authentication required" in await response.text()


@pytest.mark.asyncio
async def test_pagination_round_trip_through_http():
    async with TestClient(TestServer(application())) as client:
        token = generate_token("pagination-owner")
        rows = []
        offset = 0
        while offset is not None:
            response = await client.get(
                CATALOG_PATH, params={"token": token, "offset": offset, "limit": 2}
            )
            assert response.status == 200
            page = await response.json()
            assert page["offset"] == offset
            assert page["limit"] == 2
            assert len(page["routes"]) <= 2
            rows.extend(page["routes"])
            offset = page["next_offset"]
        whole = await (await client.get(CATALOG_PATH)).json()
        assert rows == whole["routes"]
        assert len(rows) == whole["total"]
        assert len({(row["method"], row["path"]) for row in rows}) == len(rows)
        beyond = await (await client.get(CATALOG_PATH, params={"offset": 999})).json()
        assert beyond["routes"] == []
        assert beyond["next_offset"] is None
        assert beyond["total"] == len(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"offset": 100001},
        {"limit": "no"},
        {"offset": "2.5"},
    ],
)
async def test_invalid_page_is_not_silently_coerced(query):
    async with TestClient(TestServer(application())) as client:
        response = await client.get(
            CATALOG_PATH, params={"token": generate_token("page-owner"), **query}
        )
        assert response.status == 400
        assert response.content_type == "text/plain"
        assert await response.text()


def test_removed_registration_is_not_cached():
    before = build_catalog(application(dynamic=True))
    after = build_catalog(application(dynamic=False))
    assert before["total"] == after["total"] + 3
    assert any("{record_id}" in route["path"] for route in before["routes"])
    assert all("{record_id}" not in route["path"] for route in after["routes"])
    app = application(dynamic=False)
    initial = build_catalog(app)
    app.router.add_delete("/api/new/{record_id}", api_catalog)
    updated = build_catalog(app)
    assert updated["total"] == initial["total"] + 1
    assert any(route["method"] == "DELETE" for route in updated["routes"])


def test_only_actual_metadata_and_no_static_filesystem_details(tmp_path):
    app = application()
    secret = "EXPLORER_PRIVATE_VALUE_381"
    app[web.AppKey("credential", str)] = secret
    (tmp_path / "secret.txt").write_text(secret)
    app.router.add_static("/assets/", tmp_path)
    app.router.add_get("/outside-api", api_catalog)
    catalog = build_catalog(app)
    encoded = json.dumps(catalog)
    assert secret not in encoded
    assert str(tmp_path) not in encoded
    assert "secret.txt" not in encoded
    assert "/outside-api" not in encoded
    assert "/assets/" not in encoded
    assert "__doc__" not in encoded
    assert "gideon.interfaces.dashboard" not in encoded
    row = next(
        row
        for row in catalog["routes"]
        if row["name"] == "prompt-syntax" and row["method"] == "GET"
    )
    assert row["handler"] == "api_prompt_syntax"
    assert set(row) == {"method", "path", "name", "handler", "schema", "executable"}


def test_admission_and_execution_are_separate():
    catalog = build_catalog(
        application(), admit=lambda method, path: path == CATALOG_PATH
    )
    assert {row["path"] for row in catalog["routes"]} == {CATALOG_PATH}
    assert {row["method"] for row in catalog["routes"]} == {"GET", "HEAD"}
    assert [row["method"] for row in catalog["routes"] if row["executable"]] == ["GET"]
    denied = build_catalog(application(), admit=lambda method, path: False)
    assert denied["routes"] == []
    assert denied["total"] == 0
    assert denied["next_offset"] is None
    full = build_catalog(application())
    assert all(
        not row["executable"] for row in full["routes"] if "{record_id}" in row["path"]
    )


def test_event_metadata_is_the_live_declared_contract():
    events = build_catalog(application())["events"]
    assert {row["name"] for row in events} == set(PLATFORM_EVENTS)
    for row in events:
        declaration = PLATFORM_EVENTS[row["name"]]
        assert row["payload_keys"] == list(declaration.payload_keys)
        assert row["schema"] == {"status": "partial"}
        assert row["transport"] == "app-inbox"
        assert "payload" not in row
        assert "examples" not in row
        assert "type" not in row["schema"]


@pytest.mark.asyncio
async def test_real_harmless_read_and_unknown_route_error():
    async with TestClient(TestServer(application())) as client:
        await client.get(CATALOG_PATH, params={"token": generate_token("reader")})
        catalog = await (await client.get(CATALOG_PATH)).json()
        permitted = {row["path"] for row in catalog["routes"] if row["executable"]}
        assert permitted == {CATALOG_PATH, "/api/prompts/syntax"}
        response = await client.get("/api/prompts/syntax")
        assert response.status == 200
        syntax = await response.json()
        assert isinstance(syntax, dict)
        assert "upper" in json.dumps(syntax)
        unknown = await client.get("/api/route-that-is-not-registered")
        assert unknown.status == 404
        assert "Not Found" in await unknown.text()
