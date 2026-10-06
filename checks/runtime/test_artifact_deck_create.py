"""Presentation creation uses real dashboard auth, model rendering and native artifacts."""

import asyncio
import json
import time

import pytest
import pytest_asyncio
from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer


@pytest_asyncio.fixture
async def workspace(tmp_path, monkeypatch):
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)

    from gideon.interfaces.dashboard import token_auth
    from gideon.interfaces.dashboard.handlers import auth
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.security.auth import credentials
    from gideon.workspace.artifacts import registry
    from gideon.workspace.artifacts.handlers import register_artifact_routes
    from gideon.workspace.artifacts.models import MAX_CONTENT_BYTES
    from gideon.workspace.artifacts.native import NativeArtifactProvider

    (home / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )
    credentials.set_password("slides-owner", "correct-horse-battery-staple")
    token_auth.use_ephemeral_secret()
    auth.reset_lockouts()
    previous = registry.get_provider("native")
    provider = NativeArtifactProvider(home / "artifacts")
    registry.register_provider(provider)
    state = ConsoleState(None, time.time())
    app = web.Application(
        client_max_size=MAX_CONTENT_BYTES * 2,
        middlewares=[token_auth.token_auth_middleware(port=10000)],
    )
    app["state"] = state
    app["port"] = 10000
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_post("/api/auth/login", auth.api_auth_login)
    app.router.add_get("/api/auth/session", auth.api_auth_session)
    register_artifact_routes(app)
    client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
    await client.start_server()
    try:
        login = await client.post(
            "/api/auth/login",
            json={
                "username": "slides-owner",
                "password": "correct-horse-battery-staple",
            },
        )
        assert login.status == 200, await login.text()
        session = await client.get("/api/auth/session")
        assert (
            session.status == 200 and (await session.json())["user"] == "slides-owner"
        )
        yield client, provider, state
    finally:
        await client.close()
        registry.register_provider(previous)
        auth.reset_lockouts()
        token_auth.use_persistent_secret()


def body(slug="studio-deck"):
    from gideon.workspace.documents.deck_json import deck_to_dict
    from gideon.workspace.documents.model import Bullet, DeckModel, Slide

    model = DeckModel(
        title="Studio deck",
        slides=[
            Slide(
                title="Result",
                bullets=[Bullet("Measured result", 0)],
                notes="Speaker context",
            )
        ],
    )
    return {"name": "Studio deck", "slug": slug, "model": deck_to_dict(model)}


@pytest.mark.asyncio
async def test_create_and_read_canonical_pptx(workspace):
    from gideon.workspace.documents.pptx_parser import parse_pptx

    client, provider, _state = workspace
    response = await client.post("/api/artifacts/deck", json=body())
    assert response.status == 201, await response.text()
    result = await response.json()
    assert (result["slug"], result["kind"], result["version"]) == (
        "studio-deck",
        "pptx",
        1,
    )
    stored = provider.raw_bytes("studio-deck", version=1)
    assert stored and stored[0].startswith(b"PK\x03\x04")
    parsed, _ = parse_pptx(stored[0])
    assert parsed.slides[0].title == "Result"
    assert parsed.slides[0].bullets[0].text == "Measured result"
    assert parsed.slides[0].notes == "Speaker context"
    model_response = await client.get("/api/artifacts/studio-deck/model")
    assert model_response.status == 200
    assert (await model_response.json())["version"] == 1
    raw_response = await client.get("/api/artifacts/studio-deck/raw?version=1")
    assert raw_response.status == 200
    assert await raw_response.read() == stored[0]


@pytest.mark.asyncio
async def test_auth_model_size_render_and_native_restrictions_leave_no_partial_deck(
    workspace,
):
    from gideon.workspace.artifacts.models import MAX_CONTENT_BYTES

    client, provider, state = workspace
    client.session.cookie_jar.clear()
    unauthorized = await client.post(
        "/api/artifacts/deck", json=body("unauthorized-deck")
    )
    assert unauthorized.status in (401, 403)
    assert provider.get("unauthorized-deck") is None
    login = await client.post(
        "/api/auth/login",
        json={"username": "slides-owner", "password": "correct-horse-battery-staple"},
    )
    assert login.status == 200

    state.get_or_create_session("temporary-deck", memory_mode="temporary")
    restricted = await client.post(
        "/api/artifacts/deck",
        json=body("restricted-deck"),
        headers={"X-Session-Key": "dashboard:temporary-deck"},
    )
    assert restricted.status == 403
    assert provider.get("restricted-deck") is None

    malformed = body("invalid-deck")
    malformed["model"]["slides"][0]["layout"] = "missing layout"
    response = await client.post("/api/artifacts/deck", json=malformed)
    assert response.status == 400
    assert provider.get("invalid-deck") is None

    render_failure = body("render-failed-deck")
    render_failure["model"]["template_slug"] = "missing-pptx-template"
    response = await client.post("/api/artifacts/deck", json=render_failure)
    assert response.status == 500
    assert (await response.json())["error"]["code"] == "render_failed"
    assert provider.get("render-failed-deck") is None

    oversized = await client.post(
        "/api/artifacts/deck",
        data=b"x" * (MAX_CONTENT_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status == 413
    assert provider.list(kind="pptx") == []

    frozen = provider.create(
        name="Frozen",
        content="Do not replace",
        kind="text",
        source="manual",
        slug="frozen-deck",
        readonly=True,
    )
    response = await client.post("/api/artifacts/deck", json=body("frozen-deck"))
    assert response.status == 409
    assert provider.get(frozen.slug).readonly is True
    assert provider.get(frozen.slug).content == "Do not replace"


@pytest.mark.asyncio
async def test_repeated_and_concurrent_requested_slug_never_creates_a_duplicate(
    workspace,
):
    client, provider, _state = workspace
    first = await client.post("/api/artifacts/deck", json=body("studio-deck"))
    assert first.status == 201
    repeated = await client.post("/api/artifacts/deck", json=body("studio-deck"))
    assert repeated.status == 409
    assert provider.get("studio-deck").version == 1

    responses = await asyncio.gather(
        client.post("/api/artifacts/deck", json=body("concurrent-deck")),
        client.post("/api/artifacts/deck", json=body("concurrent-deck")),
    )
    assert sorted(response.status for response in responses) == [201, 409]
    for response in responses:
        await response.read()
    assert provider.get("concurrent-deck").version == 1
    assert sorted(art.slug for art in provider.list(kind="pptx")) == [
        "concurrent-deck",
        "studio-deck",
    ]
