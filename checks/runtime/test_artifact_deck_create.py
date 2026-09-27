"""New presentation creation uses the shipped model codec, writer and artifact store."""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.artifacts import registry
from gideon.workspace.artifacts.handlers import register_artifact_routes
from gideon.workspace.artifacts.models import MAX_CONTENT_BYTES
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.documents.deck_json import deck_to_dict
from gideon.workspace.documents.model import Bullet, DeckModel, Slide
from gideon.workspace.documents.pptx_parser import parse_pptx


class ReadOnlyNativeProvider(NativeArtifactProvider):
    @property
    def readonly(self) -> bool:
        return True


@pytest.fixture
def provider(tmp_path):
    previous = registry.get_provider("native")
    current = NativeArtifactProvider(tmp_path / "artifacts")
    registry.register_provider(current)
    try:
        yield current
    finally:
        registry.register_provider(previous)


@pytest_asyncio.fixture
async def client(provider):
    app = web.Application(client_max_size=MAX_CONTENT_BYTES * 2)
    app["state"] = SimpleNamespace(_restricted_keys={"guest:restricted"}, _sessions={})
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def body(slug="studio-deck"):
    model = DeckModel(
        title="Studio deck",
        slides=[Slide(title="Result", bullets=[Bullet("Measured result", 0)], notes="Speaker context")],
    )
    return {"name": "Studio deck", "slug": slug, "model": deck_to_dict(model)}


@pytest.mark.asyncio
async def test_create_and_read_canonical_pptx(client, provider):
    response = await client.post("/api/artifacts/deck", json=body())
    assert response.status == 201, await response.text()
    result = await response.json()
    assert (result["slug"], result["kind"], result["version"]) == ("studio-deck", "pptx", 1)
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
async def test_rejects_invalid_duplicate_restricted_and_oversized_without_artifact(client, provider):
    malformed = body("invalid-deck")
    malformed["model"]["slides"][0]["layout"] = "missing layout"
    response = await client.post("/api/artifacts/deck", json=malformed)
    assert response.status == 400
    assert provider.get("invalid-deck") is None

    restricted = await client.post("/api/artifacts/deck", json=body("restricted-deck"), headers={"X-Session-Key": "guest:restricted"})
    assert restricted.status == 403
    assert provider.get("restricted-deck") is None

    oversized = await client.post("/api/artifacts/deck", data=b"x" * (MAX_CONTENT_BYTES + 1), headers={"Content-Type": "application/json"})
    assert oversized.status == 413
    assert provider.list(kind="pptx") == []

    first = await client.post("/api/artifacts/deck", json=body())
    assert first.status == 201
    duplicate = await client.post("/api/artifacts/deck", json=body())
    assert duplicate.status == 409
    assert provider.get("studio-deck").version == 1


@pytest.mark.asyncio
async def test_readonly_provider_refuses_before_render(client, provider, tmp_path):
    registry.register_provider(ReadOnlyNativeProvider(tmp_path / "readonly"))
    try:
        response = await client.post("/api/artifacts/deck", json=body("readonly-deck"))
        assert response.status == 400
        assert provider.get("readonly-deck") is None
    finally:
        registry.register_provider(provider)
