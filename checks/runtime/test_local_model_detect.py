"""Local-model bindings synchronize configured providers into the live registry."""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.integrations.llm import registry as llm_registry
from gideon.interfaces.dashboard.handlers import model_registry


@pytest.mark.asyncio
async def test_local_model_detect(tmp_path, monkeypatch) -> None:
    """A configured local provider is live as soon as Settings binds one of its models."""
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"providers": [{"name": "local", "type": "local-model"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("gideon.core.config.loader.config_path", lambda: config)
    monkeypatch.setattr(model_registry, "_sel_log", lambda *args, **kwargs: None)

    previous = llm_registry.get_default_registry()
    llm_registry.set_default_registry(llm_registry.ProviderRegistry())
    try:
        request = make_mocked_request(
            "PUT",
            "/api/models/active/chat",
            headers={"Content-Type": "application/json"},
        )
        request.match_info["use_case"] = "chat"
        request._read_bytes = json.dumps({"models": ["local:llama"]}).encode()

        response = await model_registry.api_models_active_set(request)

        assert response.status == 200
        assert (
            llm_registry.get_default_registry().get_entry("local").type == "local-model"
        )
    finally:
        llm_registry.set_default_registry(previous)
