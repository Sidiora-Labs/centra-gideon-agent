import importlib.util
import json
import socket
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.providers.provider_bridge import (
    ProviderResolutionError,
    resolve_provider_for_use_case,
)
from gideon.integrations.llm.registry import (
    get_default_registry,
    sync_entries_from_config,
)
from gideon.interfaces.dashboard.handlers.model_registry import (
    api_onboarding_model_check,
)
from gideon.interfaces.dashboard.handlers.providers import api_provider_test


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_AGENT_ID", raising=False)
    monkeypatch.delenv("GIDEON_SESSION_KEY", raising=False)
    registry = get_default_registry()
    previous = dict(registry._entries)
    registry._entries.clear()
    yield tmp_path
    registry._entries.clear()
    registry._entries.update(previous)


def write_home(home, providers, chat):
    (home / "config.json").write_text(json.dumps({"providers": providers}))
    (home / "active_models.json").write_text(json.dumps({"chat": chat}))
    sync_entries_from_config()


async def call():
    app = web.Application()
    app.router.add_get("/check", api_onboarding_model_check)
    app.router.add_post("/providers/{name}/test", api_provider_test)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_empty_home_refuses_actual_resolution(home):
    write_home(home, [], [])
    async with await call() as client:
        response = await client.get("/check")
        result = await response.json()
    assert response.status == 200 and result["ok"] is False
    assert result["why"] and result["fix"]


@pytest.mark.asyncio
async def test_bound_but_unregistered_provider_is_not_ready(home):
    write_home(
        home,
        [{"name": "my-openai", "type": "missing-provider-app", "model": "gpt-4o-mini"}],
        ["my-openai:gpt-4o-mini"],
    )
    with pytest.raises(ProviderResolutionError) as caught:
        resolve_provider_for_use_case("chat")
    async with await call() as client:
        result = await (await client.get("/check")).json()
    assert result["ok"] is False
    envelope = caught.value.agent_error
    if envelope is not None:
        assert result["why"] == envelope.to_dict()["why"]
        assert result["fix"] == envelope.to_dict()["fix"]


@pytest.mark.asyncio
async def test_real_ollama_build_names_responder_but_closed_endpoint_probe_refuses(
    home,
):
    source = (
        Path(__file__).parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    spec = importlib.util.spec_from_file_location("gideon_onboarding_ollama", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    write_home(
        home,
        [
            {
                "name": "local-ollama",
                "type": "ollama",
                "model": "qwen2.5:0.5b",
                "options": {"endpoint": f"http://127.0.0.1:{port}"},
            }
        ],
        ["local-ollama:qwen2.5:0.5b"],
    )
    async with await call() as client:
        built = await (await client.get("/check")).json()
        probe = await (
            await client.post("/providers/local-ollama/test", json={})
        ).json()
    assert built["ok"] is True and built["source"] == "binding"
    assert built["provider"] == "local-ollama" and built["model"] == "qwen2.5:0.5b"
    assert probe["ok"] is False and probe["message"]


@pytest.mark.parametrize("raw", ["{not json", "[]"])
@pytest.mark.asyncio
async def test_unreadable_binding_is_unknown_and_never_ready(home, raw):
    write_home(home, [], [])
    (home / "active_models.json").write_text(raw)
    async with await call() as client:
        result = await (await client.get("/check")).json()
    assert result["ok"] is False and result["code"] == "read_failed"
    assert result["fix"].startswith("Retry")
