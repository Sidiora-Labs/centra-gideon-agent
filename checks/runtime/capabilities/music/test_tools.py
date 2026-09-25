import importlib
import json
from pathlib import Path

import pytest
from gideon.sdk.tool import RiskLevel, ToolProvider
from gideon.workspace.capabilities.music.tools import MusicToolProvider
from test_catalog import artifact, attach_body, catalog_at
from test_repertoire import attempt, store_at


def provider_at(home):
    return MusicToolProvider(repertoire=store_at(home), catalog=catalog_at(home))


@pytest.mark.asyncio
async def test_native_manifest_resolves_factory_and_exposes_complete_tools():
    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / 'runtime/gideon/extensions/apps/native/gideon-music-tools/app.json').read_text())
    module, name = manifest['provider']['implementation'].split(':')
    provider = getattr(importlib.import_module(module), name)()
    assert isinstance(provider, ToolProvider)
    assert provider.name == manifest['name']
    definitions = await provider.list_tools()
    names = {tool.name for tool in definitions}
    assert len(names) == 11
    assert 'music_repertoire_practice' in names
    assert 'music_catalog_attach' in names
    assert 'music_catalog_select' in names
    for tool in definitions:
        assert tool.parameters['additionalProperties'] is False
        assert 'home' not in tool.parameters['properties']
        assert 'root' not in tool.parameters['properties']
        if tool.name.endswith(('_list', '_get')):
            assert tool.risk_level == RiskLevel.SAFE
            assert tool.requires_approval is False
        else:
            assert tool.risk_level == RiskLevel.CAUTION
            assert tool.requires_approval is True


@pytest.mark.asyncio
async def test_native_repertoire_author_practice_replay_and_edit(tmp_path):
    provider = provider_at(tmp_path)
    created = await provider.invoke('music_repertoire_create', {'data': {'title': 'Agent repertoire', 'body': 'D G A'}})
    assert created.success
    item = json.loads(created.output)
    payload = attempt(item)
    practiced = await provider.invoke('music_repertoire_practice', {'id': item['id'], 'data': payload})
    assert practiced.success
    receipt = json.loads(practiced.output)
    assert receipt['item']['stage'] == 'learning'
    replay = await provider.invoke('music_repertoire_practice', {'id': item['id'], 'data': payload})
    assert json.loads(replay.output) == {**receipt, 'replayed': True}
    edited = await provider.invoke('music_repertoire_update', {'id': item['id'], 'data': {'revision': 2, 'body': 'New fingering'}})
    assert edited.success
    reloaded = await provider_at(tmp_path).invoke('music_repertoire_get', {'id': item['id']})
    assert json.loads(reloaded.output)['body'] == 'New fingering'
    assert json.loads(reloaded.output)['practice_history'] == receipt['item']['practice_history']
    listing = await provider.invoke('music_repertoire_list', {'limit': 1})
    assert len(json.loads(listing.output)) == 1


@pytest.mark.asyncio
async def test_native_catalog_operations_reach_same_actual_stores(tmp_path):
    provider = provider_at(tmp_path)
    artist = await provider.invoke('music_catalog_create', {'kind': 'artists', 'data': {'name': 'Native artist'}})
    assert artist.success
    artist_id = json.loads(artist.output)['id']
    created = await provider.invoke('music_catalog_create', {'kind': 'tracks', 'data': {'title': 'Native track', 'artist_id': artist_id}})
    track = json.loads(created.output)
    audio = artifact(provider.catalog)
    attached = await provider.invoke('music_catalog_attach', {'id': track['id'], 'data': attach_body(track, audio)})
    assert attached.success
    value = json.loads(attached.output)
    render_id = value['renders'][0]['id']
    selected = await provider.invoke('music_catalog_select', {'id': track['id'], 'data': {'revision': 2, 'render_id': render_id}})
    assert selected.success
    assert json.loads(selected.output)['selected_render_id'] == render_id
    album = await provider.invoke('music_catalog_create', {'kind': 'albums', 'data': {'title': 'Native album', 'track_ids': [track['id']]}})
    assert json.loads(album.output)['track_ids'] == [track['id']]
    archived = await provider.invoke('music_catalog_update', {'kind': 'tracks', 'id': track['id'], 'data': {'revision': 3, 'archived': True}})
    assert archived.success
    listing = await provider.invoke('music_catalog_list', {'kind': 'tracks', 'archived': True, 'q': 'Native'})
    assert json.loads(listing.output) == [json.loads(archived.output)]
    fetched = await provider_at(tmp_path).invoke('music_catalog_get', {'kind': 'tracks', 'id': track['id']})
    assert json.loads(fetched.output)['renders'][0]['duration_seconds'] == 1


@pytest.mark.asyncio
async def test_tool_errors_preserve_conflict_and_unavailable_status(tmp_path):
    provider = provider_at(tmp_path)
    item = provider.repertoire.create({'title': 'Conflict'})
    stale = await provider.invoke('music_repertoire_update', {'id': item['id'], 'data': {'revision': 99, 'title': 'Stale'}})
    assert stale.success is False
    assert stale.metadata == {'code': 'revision_conflict', 'status': 409}
    missing = await provider.invoke('music_catalog_get', {'kind': 'tracks', 'id': 'missing'})
    assert missing.metadata['status'] == 404
    track = provider.catalog.create('tracks', {'title': 'No generation'})
    unavailable = await provider.invoke('music_catalog_attach', {'id': track['id'], 'data': {'revision': 1, 'artifact_ref': {}, 'source': {'kind': 'generated'}}})
    assert unavailable.success is False
    assert unavailable.metadata['code'] == 'generation_provenance_unavailable'
    assert unavailable.metadata['status'] == 503


@pytest.mark.asyncio
async def test_invocation_rejects_home_override_and_unknown_tools(tmp_path):
    provider = provider_at(tmp_path)
    for arguments in ({'root': '/tmp'}, {'home': '/tmp'}, {'path': '/tmp'}, {'provider': 'other'}):
        result = await provider.invoke('music_repertoire_list', arguments)
        assert result.success is False
        assert result.metadata['code'] == 'invalid_input'
    unknown = await provider.invoke('music_delete_all', {})
    assert unknown.success is False
    assert unknown.metadata['code'] == 'unknown_tool'
    missing_args = await provider.invoke('music_catalog_create', {'data': {'title': 'Missing kind'}})
    assert missing_args.success is False
    assert provider.catalog.list('tracks') == []


@pytest.mark.asyncio
async def test_isolated_tool_providers_cannot_read_other_home(tmp_path):
    first, second = provider_at(tmp_path / 'one'), provider_at(tmp_path / 'two')
    created = await first.invoke('music_repertoire_create', {'data': {'title': 'First home'}})
    item = json.loads(created.output)
    missing = await second.invoke('music_repertoire_get', {'id': item['id']})
    assert missing.success is False
    assert missing.metadata['status'] == 404
    assert second.repertoire.list() == []
    found = await first.invoke('music_repertoire_get', {'id': item['id']})
    assert json.loads(found.output) == item
