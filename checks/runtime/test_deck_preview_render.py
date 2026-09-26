"""The saved PPTX preview route renders and persists real Chromium PNGs."""

import io
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.workspace.artifacts import registry
from gideon.workspace.artifacts.handlers import register_artifact_routes
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.documents.model import Bullet, DeckModel, Slide
from gideon.workspace.documents.writers.pptx_writer import render_pptx

MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.asyncio
async def test_saved_deck_renders_real_version_bound_slide_png(tmp_path, monkeypatch):
    provider = NativeArtifactProvider(tmp_path / "artifacts")
    monkeypatch.setattr(registry, "get_provider", lambda name=None: provider)
    figure = io.BytesIO()
    Image.new("RGB", (320, 200), "#2879bb").save(figure, format="PNG")
    image = provider.create_binary(name="Figure", data=figure.getvalue(), mime="image/png")
    model = DeckModel(slides=[Slide(title="Finding", bullets=[Bullet("Source result " * 150)], artifact_slug=image.slug)])
    deck = provider.create_binary(name="Report deck", data=render_pptx(model), mime=MIME, kind="pptx")

    app = web.Application()
    app["state"] = SimpleNamespace(_restricted_keys=set(), _sessions={})
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        url = f"/api/artifacts/{deck.slug}/deck-preview"
        response = await client.post(url, headers={"If-Match": "1"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload["version"] == 1
        assert "not represented" in payload["fidelity"]
        assert len(payload["slides"]) == 1
        preview = payload["slides"][0]
        assert any("overflows" in note for note in preview["critique"])
        assert provider.get(preview["slug"]).kind == "image"
        png_response = await client.get(preview["raw_url"])
        png = await png_response.read()
        assert png_response.status == 200 and png.startswith(b"\x89PNG\r\n\x1a\n")
        rendered = Image.open(io.BytesIO(png)).convert("RGB")
        assert rendered.size[0] == 1280
        assert rendered.getpixel((1000, 500))[2] > rendered.getpixel((1000, 500))[0]

        again = await client.post(url, headers={"If-Match": "1"})
        assert (await again.json())["slides"][0]["slug"] == preview["slug"]
        provider.update_binary(deck.slug, data=render_pptx(DeckModel(slides=[Slide(title="Revised")])), mime=MIME)
        stale = await client.post(url, headers={"If-Match": "1"})
        assert stale.status == 409
        revised = await client.post(url, headers={"If-Match": "2"})
        assert revised.status == 200
        assert (await revised.json())["slides"][0]["slug"] != preview["slug"]
    finally:
        await client.close()
