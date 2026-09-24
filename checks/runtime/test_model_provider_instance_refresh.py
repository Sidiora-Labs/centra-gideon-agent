import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine import agent
from gideon.extensions.apps.manifest import AppManifest, ProviderConfig
from gideon.extensions.providers import instance_routes, loader, registry
from gideon.integrations.tool_providers import registry as tool_registry
from gideon.integrations.tts import registry as tts_registry
from gideon.integrations.tts.openai_provider import OpenAITtsProvider


@pytest.fixture
def providers(tmp_path, monkeypatch):
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(agent, "_USER_DIR", tmp_path)
    monkeypatch.setattr(agent, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(agent, "_USER_MCP_JSON", tmp_path / "mcp.json")
    monkeypatch.setattr(agent, "_USER_PROMPT", tmp_path / "prompt.md")
    monkeypatch.setattr(agent, "_USER_OVERRIDES", tmp_path / "agent.json")
    monkeypatch.setattr(agent, "_DEFAULT_HOOKS_DIR", tmp_path / "hooks")
    monkeypatch.setattr(loader, "BUNDLED_DIR", tmp_path / "apps")
    monkeypatch.setattr(tts_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_providers", {})
    live = registry.ProviderRegistry()
    live.register_type_handler("model", registry.ModelTypeHandler())
    live.register_type_handler("tool", registry.ToolTypeHandler())
    monkeypatch.setattr(registry, "_registry", live)
    model_app = tmp_path / "apps" / "instance-speech"
    model_app.mkdir(parents=True)
    (model_app / "provider.py").write_text(
        "from gideon.integrations.tts.openai_provider import OpenAITtsProvider\n"
        "def create_provider(config):\n"
        '    return OpenAITtsProvider(provider_name=config["name"], '
        'endpoint=config["endpoint"])\n'
    )
    live.register(
        AppManifest(
            name="instance-speech",
            provider=ProviderConfig(
                type="model",
                multiInstance=True,
                capabilities=["tts"],
                implementation="provider:create_provider",
            ),
        ),
        enabled=True,
    )
    live.register(
        AppManifest(
            name="instance-tools",
            provider=ProviderConfig(
                type="tool",
                multiInstance=True,
                implementation="gideon.integrations.tool_providers.code_map:create_code_map_provider",
            ),
        ),
        enabled=True,
    )
    return live, tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["instance-speech", "instance-tools"])
async def test_instance_mutations_refresh_live_providers(providers, name):
    live, home = providers
    app = web.Application()
    instance_routes.register_instance_routes(app)
    base = f"/api/providers/{name}/instances"
    domain = tts_registry if name == "instance-speech" else tool_registry
    key = "primary" if name == "instance-speech" else "workflows-tools"
    assert live.get(name).provider_instance is None
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            base,
            json={
                "display_name": "Primary",
                "config": {"name": "primary", "endpoint": "http://127.0.0.1:9001"},
            },
        )
        assert response.status == 201, await response.text()
        identity = (await response.json())["instance"]["id"]
        path = f"{base}/{identity}"
        first = domain.get_provider(key)
        assert first is not None
        assert first.instance_id == identity
        assert (home / "agents" / agent.AGENT_FILENAME).is_file()
        if name == "instance-speech":
            assert isinstance(first, OpenAITtsProvider)
            assert first._endpoint == "http://127.0.0.1:9001"

        response = await client.put(
            path,
            json={
                "config": {"name": "primary", "endpoint": "http://127.0.0.1:9002"},
            },
        )
        assert response.status == 200, await response.text()
        updated = domain.get_provider(key)
        assert updated is not None and updated is not first
        if name == "instance-speech":
            assert updated._endpoint == "http://127.0.0.1:9002"

        response = await client.put(path, json={"enabled": False})
        assert response.status == 200, await response.text()
        assert domain.get_provider(key) is None
        assert live.get(name).provider_instance is None

        response = await client.put(path, json={"enabled": True})
        assert response.status == 200, await response.text()
        assert domain.get_provider(key) is not None

        response = await client.delete(path)
        assert response.status == 200, await response.text()
        assert domain.get_provider(key) is None
        assert live.get(name).provider_instance is None
        assert not live.get(name).error
