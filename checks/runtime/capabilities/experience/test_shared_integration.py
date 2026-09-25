import json
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import RegisteredProvider, ToolTypeHandler
from gideon.interfaces.dashboard.handlers.capabilities import register as register_all
from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.workspace.capabilities.experience.game_assets_provider import GameAssetTools
from gideon.workspace.capabilities.experience.moltworld_provider import MoltworldTools
from gideon.workspace.capabilities.experience.store import ExperienceStore


async def test_game_assets_route_and_native_provider_dispatch_from_shared_registration(tmp_path):
    app = web.Application()
    app[STORE] = ExperienceStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/capabilities/experience/game-assets/projects')
        assert response.status == 200
        assert await response.json() == {
            'projects': [],
            'required_roles': ['sprite', 'artwork', 'music', 'model'],
        }

    provider = GameAssetTools(store=ExperienceStore(tmp_path))
    result = await provider.invoke('experience_game_assets_get', {})
    assert result.success is True
    assert json.loads(result.output) == {
        'projects': [],
        'required_roles': ['sprite', 'artwork', 'music', 'model'],
    }
    refused = await provider.invoke('experience_game_assets_compile', {'id': 'missing', 'revision': 1})
    assert refused.success is False
    assert 'not found' in refused.error.lower()

    manifest = AppManifest.from_json_file(
        Path(__file__).parents[4]
        / 'runtime/gideon/extensions/apps/native/gideon-experience/app.json'
    )
    game = next(
        item for item in manifest.providers
        if item.implementation.endswith('game_assets_provider:create_provider')
    )
    assert game.capabilities == [
        'experience_game_assets_get',
        'experience_game_assets_compile',
        'experience_game_assets_publish',
    ]
    assert 'downloadable GLB assets' in manifest.description


async def test_moltworld_route_and_secondary_native_provider_use_shared_experience_contract(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    app = web.Application()
    app[STORE] = ExperienceStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/capabilities/experience/moltworld')
        assert response.status == 200
        snapshot = await response.json()
        assert snapshot['readiness']['protocol'] == 'moltworld-v1-2026-09-25'
        assert snapshot['readiness']['remote_status'] == 'unverified'
        assert snapshot['history'] == []

    manifest = AppManifest.from_json_file(
        Path(__file__).parents[4]
        / 'runtime/gideon/extensions/apps/native/gideon-experience/app.json'
    )
    config = next(
        item for item in manifest.providers
        if item.implementation.endswith('moltworld_provider:create_provider')
    )
    record = RegisteredProvider(name=manifest.name, manifest=manifest, provider_config=config)
    provider = ToolTypeHandler().create(record)
    assert isinstance(provider, MoltworldTools)
    definitions = {item.name: item for item in await provider.list_tools()}
    assert definitions['experience_moltworld_get'].requires_approval is False
    assert definitions['experience_moltworld_status'].requires_approval is False
    assert definitions['experience_moltworld_observe'].requires_approval is False
    assert definitions['experience_moltworld_configure'].requires_approval is True
    assert definitions['experience_moltworld_action'].requires_approval is True
    assert config.capabilities == list(definitions)

    @web.middleware
    async def owner(request, handler):
        request['user'] = 'experience-owner'
        return await handler(request)

    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    assembled = web.Application(middlewares=[owner])
    assembled['state'] = state
    register_all(assembled)
    try:
        async with TestClient(TestServer(assembled)) as client:
            response = await client.get('/api/capabilities/experience/moltbook/config')
            assert response.status == 200
            assert await response.json() == {
                'configured': False,
                'base_url': 'https://www.moltbook.com/api/v1',
                'registration_supported': False,
                'external_qualified': False,
            }
    finally:
        state.knowledge_store.close()
