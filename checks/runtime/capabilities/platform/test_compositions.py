import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard import views_store as s
from gideon.interfaces.dashboard.handlers.capabilities_compositions import register
from gideon.interfaces.dashboard.handlers import views
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_ephemeral_secret
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/compositions'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    return tmp_path


def patch(tiles):
    return {'revision': s.composition_state()['revision'], 'tiles': tiles}


def select(identifier):
    return s.set_composition(identifier, {'revision': s.composition_state()['revision'], 'select': True})


def test_canonical_store_default_presets_and_order_size_membership(home):
    initial = s.composition_state()
    assert initial['selected_view'] == 'overview'
    assert initial['revision'] == 0
    assert initial['views'][0]['preset'] is True
    assert not s.views_path().exists()
    view = s.create_view('Operations')
    result = s.set_composition(view.id, patch([{'ref': 'core:tasks', 'size': 'full'}, {'ref': 'core:system-health', 'size': 's'}]))
    assert result['revision'] == 1
    stored = s.get_view(view.id)
    assert [(tile.ref, tile.size, tile.order) for tile in stored.tiles] == [('core:tasks', 'full', 0), ('core:system-health', 's', 1)]
    result = select(view.id)
    assert result['selected_view'] == view.id
    assert result['revision'] == 2
    disk = json.loads((home / 'dashboard_views.json').read_text())
    assert disk['selected_view'] == view.id
    assert disk['composition_revision'] == 2
    assert disk['views'][0]['tiles'][0]['ref'] == 'core:tasks'
    assert not (home / 'capabilities/platform').exists()
    reordered = s.set_composition(view.id, patch([{'ref': 'core:system-health', 'size': 'l'}, {'ref': 'core:tasks', 'size': 'm'}]))
    assert reordered['selected_view'] == view.id
    assert [tile.ref for tile in s.get_view(view.id).tiles] == ['core:system-health', 'core:tasks']
    assert [tile.size for tile in s.get_view(view.id).tiles] == ['l', 'm']
    s.set_composition(view.id, patch([]))
    assert s.get_view(view.id).tiles == []
    assert s.composition_state()['selected_view'] == view.id


def test_artifact_overlay_survives_core_edit_and_resolves_in_selected_view(home):
    view = s.create_view('Work')
    s.add_tile(view.id, 'artifact:sample', added_by='agent')
    s.add_tile('overview', 'artifact:other')
    assert s.find_tile(view.id, 'artifact:sample') is not None
    s.set_tile_refresh(view.id, 'artifact:sample', {'mode': 'ttl', 'ttl_secs': 60})
    s.set_composition(view.id, patch([{'ref': 'core:tasks', 'size': 'm'}]))
    select(view.id)
    tile = s.find_tile(view.id, 'artifact:sample')
    assert tile.added_by == 'agent'
    assert tile.refresh.ttl_secs == 60
    s.resolve_tile(view.id, 'artifact:sample', keep=True)
    assert s.find_tile(view.id, 'artifact:sample').added_by == 'user'
    s.resolve_tile(view.id, 'artifact:sample', keep=False)
    assert s.find_tile(view.id, 'artifact:sample') is None
    assert s.find_tile('overview', 'artifact:other') is not None
    assert [tile.ref for tile in s.get_view(view.id).tiles] == ['core:tasks']
    assert s.composition_state()['selected_view'] == view.id
    s.update_view(view.id, {'name': 'Updated work'})
    assert s.get_view(view.id).name == 'Updated work'
    assert s.get_view(view.id).tiles[0].ref == 'core:tasks'


@pytest.mark.parametrize('tiles', [[{'ref': 'core:unknown', 'size': 'm'}], [{'ref': 'core:tasks', 'size': 'x'}], [{'ref': 'artifact:sample', 'size': 'm'}], [{'ref': 'core:tasks', 'size': 'm', 'x': 1}], [{'ref': 'core:tasks', 'size': 'm'}, {'ref': 'core:tasks', 'size': 's'}]])
def test_invalid_membership_rejected_without_rewriting_store(home, tiles):
    view = s.create_view('Work')
    before = s.views_path().read_bytes()
    with pytest.raises(ValueError):
        s.set_composition(view.id, patch(tiles))
    assert s.views_path().read_bytes() == before
    assert s.composition_state()['revision'] == 0
    assert s.get_view(view.id).tiles == []


def test_immutable_presets_stale_revision_and_deleted_selection_fallback(home):
    view = s.create_view('Work')
    before = s.get_view('overview')
    with pytest.raises(s.PresetLockedError):
        s.set_composition('overview', patch([]))
    assert s.get_view('overview') == before
    select(view.id)
    with pytest.raises(ValueError, match='changed'):
        s.set_composition(view.id, {'revision': 0, 'tiles': []})
    with pytest.raises(s.ViewNotFoundError):
        select('mission-control')
    with pytest.raises(s.ViewNotFoundError):
        select('missing')
    s.delete_view(view.id)
    assert s.composition_state()['selected_view'] == 'overview'
    assert s.get_view('overview') == before


@pytest.mark.asyncio
async def test_signed_real_composition_and_existing_view_http_paths(home):
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    app.router.add_get('/api/dashboard/views', views.api_dashboard_views)
    app.router.add_post('/api/dashboard/views/{view_id}/tiles/resolve', views.api_dashboard_view_tile_resolve)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        response = await client.get(PREFIX, params={'token': generate_token('owner')})
        assert response.status == 200
        assert (await response.json())['selected_view'] == 'overview'
        response = await client.post(PREFIX, json={'name': 'HTTP composition'})
        assert response.status == 200
        view = next(row for row in (await response.json())['views'] if not row['preset'])
        response = await client.put(PREFIX + '/' + view['id'], json={'revision': 0, 'tiles': [{'ref': 'core:schedule', 'size': 'full'}]})
        assert response.status == 200
        assert (await response.json())['revision'] == 1
        response = await client.put(PREFIX + '/' + view['id'], json={'revision': 1, 'select': True})
        assert response.status == 200
        assert (await response.json())['selected_view'] == view['id']
        s.add_tile(view['id'], 'artifact:real-overlay')
        response = await client.get('/api/dashboard/views')
        stored = next(row for row in (await response.json())['views'] if row['id'] == view['id'])
        assert [tile['ref'] for tile in stored['tiles']] == ['core:schedule', 'artifact:real-overlay']
        response = await client.post('/api/dashboard/views/' + view['id'] + '/tiles/resolve', json={'ref': 'artifact:real-overlay', 'keep': False})
        assert response.status == 200
        assert [tile['ref'] for tile in (await response.json())['view']['tiles']] == ['core:schedule']
        assert (await client.put(PREFIX + '/overview', json={'revision': 2, 'tiles': []})).status == 409
        assert (await client.post(PREFIX, json={'name': ''})).status == 409
        assert s.composition_state()['selected_view'] == view['id']


@pytest.mark.asyncio
async def test_native_selection_uses_canonical_view_and_actor(home):
    view = s.create_view('Native view')
    provider = create_provider()
    read = await provider.invoke('platform_dashboard_compositions', {})
    assert read.success
    assert json.loads(read.output)['selected_view'] == 'overview'
    token = set_current_session_key('agent:dashboard')
    try:
        changed = await provider.invoke('platform_dashboard_select', {'view_id': view.id, 'revision': 0})
        assert changed.success
        assert json.loads(changed.output)['selected_view'] == view.id
    finally:
        reset_current_session_key(token)
    token = set_current_session_key('')
    try:
        denied = await provider.invoke('platform_dashboard_select', {'view_id': 'overview', 'revision': 1})
        assert not denied.success
    finally:
        reset_current_session_key(token)
    assert s.composition_state()['selected_view'] == view.id
    tool = next(item for item in await provider.list_tools() if item.name == 'platform_dashboard_select')
    assert tool.requires_approval
    manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
    assert tool.name in manifest['provider']['capabilities']
