"""Whole settings records carry the revision of the exact value the page read."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def settings_home(tmp_path, monkeypatch):
    from gideon.extensions.providers import use_cases

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"providers": [{"name": "Ollama", "type": "ollama"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    use_cases.save_active_models({"chat": ["Ollama:llama3"]})
    return tmp_path


@pytest.fixture
def settings_app():
    from gideon.interfaces.dashboard.handlers.model_registry import (
        api_models_active,
        api_models_active_set,
    )

    app = web.Application()
    app.router.add_get("/api/models/active", api_models_active)
    app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
    return app


async def _read_chain(client: TestClient) -> tuple[list[str], str]:
    response = await client.get("/api/models/active")
    assert response.status == 200
    body = await response.json()
    return body["use_cases"]["chat"], body["revisions"]["chat"]


async def test_a_second_model_chain_save_from_the_same_revision_is_refused(
    settings_home, settings_app
):
    from gideon.extensions.providers.use_cases import load_active_models

    async with TestClient(TestServer(settings_app)) as client:
        original, revision = await _read_chain(client)
        first = await client.put(
            "/api/models/active/chat",
            json={"models": [*original, "Ollama:qwen3"]},
            headers={"If-Match": f'"{revision}"'},
        )
        assert first.status == 200

        stale = await client.put(
            "/api/models/active/chat",
            json={"models": ["Ollama:llama3", "Ollama:deepseek-r1"]},
            headers={"If-Match": f'W/"{revision}"'},
        )
        assert stale.status == 409
        error = await stale.json()
        assert error["error"]["code"] == "stale_write"
        assert "revision" not in json.dumps(error)

    assert load_active_models()["chat"] == ["Ollama:llama3", "Ollama:qwen3"]


async def test_a_model_chain_write_without_a_read_revision_is_refused(
    settings_home, settings_app
):
    from gideon.extensions.providers.use_cases import load_active_models

    async with TestClient(TestServer(settings_app)) as client:
        response = await client.put(
            "/api/models/active/chat", json={"models": ["Ollama:qwen3"]}
        )
        assert response.status == 428
        assert (await response.json())["error"]["code"] == "revision_required"

    assert load_active_models()["chat"] == ["Ollama:llama3"]
