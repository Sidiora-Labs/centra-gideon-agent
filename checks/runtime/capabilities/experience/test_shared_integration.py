import json
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps.manifest import AppManifest
from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience.game_assets_provider import GameAssetTools
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
