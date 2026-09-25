import asyncio
import copy
import json
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.moodboards import BoardStore
from gideon.workspace.capabilities.creative.universes import UniverseStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider

BASE = '/api/capabilities/creative/universes'


def data(**changes):
    return {'request_id': str(uuid4()), 'title': 'Aster universe', 'canon': [
        {'id': 'night', 'title': 'Permanent night', 'body': 'The city has no sunrise.'},
        {'id': 'moon', 'title': 'Moon', 'body': 'One red moon.'}],
        'visual_identity': {'colors': ['#AABBCC'], 'style_notes': 'Copper and glass.'}, **changes}


def linked(home):
    ingredients = IngredientStore(home)
    ingredient = ingredients.create({'request_id': 'city', 'type': 'place', 'title': 'City'})
    boards = BoardStore(home)
    board = boards.create({'request_id': 'palette', 'title': 'Palette'})
    store = UniverseStore(home)
    payload = data(ingredient_ids=[ingredient['id']], board_refs=[{'id': board['id'], 'revision': 1}])
    return store, ingredients, boards, ingredient, board, payload


def test_universe_canon_identity_order_restore_and_reopen(tmp_path):
    store, ingredients, boards, ingredient, board, payload = linked(tmp_path)
    first = store.create(payload)
    assert first['revision'] == 1
    assert first['visual_identity']['colors'] == ['#aabbcc']
    assert first['canon'] == payload['canon']
    assert store.path == ingredients.path == boards.path
    second = store.update(first['id'], {'revision': 1, 'canon': list(reversed(first['canon'])),
                                      'visual_identity': {'colors': ['#112233', '#abcdef'], 'style_notes': 'Glass only.'}})
    assert second['revision'] == 2
    assert [entry['id'] for entry in second['canon']] == ['moon', 'night']
    assert second['visual_identity']['style_notes'] == 'Glass only.'
    assert second['created_at'] == first['created_at']
    assert store.export(first['id'], 1) == first
    assert store.export(first['id'], 2) == second
    restored = store.restore(first['id'], {'revision': 2, 'target_revision': 1})
    assert restored['canon'] == first['canon']
    assert restored['visual_identity'] == first['visual_identity']
    assert restored['revision'] == 3
    reopened = UniverseStore(tmp_path)
    assert reopened.export(first['id']) == restored
    assert reopened.revisions(first['id']) == [restored, second, first]
    assert reopened.get(first['id'])['ingredient_status'] == [{'id': ingredient['id'], 'missing': False}]
    assert reopened.get(first['id'])['board_status'] == [{'id': board['id'], 'revision': 1, 'missing': False}]


def test_reference_pin_stays_at_selected_revision_and_missing_is_explicit(tmp_path):
    store, ingredients, boards, ingredient, board, payload = linked(tmp_path)
    first = store.create(payload)
    boards.update(board['id'], {'revision': 1, 'title': 'New palette'})
    assert store.get(first['id'])['board_refs'] == [{'id': board['id'], 'revision': 1}]
    assert boards.export(board['id'], 1)['title'] == 'Palette'
    with store.connection() as db:
        db.execute('DELETE FROM ingredients WHERE id=?', (ingredient['id'],))
        db.execute('DELETE FROM board_revisions WHERE id=?', (board['id'],))
    missing = store.get(first['id'])
    assert missing['ingredient_status'][0]['missing'] is True
    assert missing['board_status'][0]['missing'] is True
    assert store.export(first['id']) == first
    second = store.update(first['id'], {'revision': 1, 'ingredient_ids': [], 'board_refs': []})
    assert second['ingredient_ids'] == []
    restored = store.restore(first['id'], {'revision': 2, 'target_revision': 1})
    assert restored['board_refs'] == first['board_refs']
    assert restored['ingredient_ids'] == first['ingredient_ids']
    with pytest.raises(CatalogError):
        store.create(data(ingredient_ids=[ingredient['id']]))
    with pytest.raises(CatalogError):
        store.create(data(board_refs=[{'id': board['id'], 'revision': 1}]))


@pytest.mark.parametrize('changes', [
    {'canon': [{'id': 'one', 'title': 'One'}, {'id': 'one', 'title': 'Duplicate'}]},
    {'canon': [{'id': '../escape', 'title': 'Bad'}]},
    {'canon': [{'id': 'one', 'title': ''}]},
    {'canon': [{'id': 'one', 'title': 'One', 'source': '/tmp/secret'}]},
    {'canon': 'invalid'}, {'canon': [{}] * 101},
    {'visual_identity': {'colors': ['red']}}, {'visual_identity': {'colors': [123]}},
    {'visual_identity': {'colors': ['#aabbcc'] * 21}},
    {'visual_identity': {'provider': 'override'}},
    {'board_refs': [{'id': 'missing', 'revision': True}]},
    {'board_refs': [{'id': 'missing', 'revision': 0}]},
    {'ingredient_ids': ['../secret']}, {'home': '/tmp/escape'}, {'account': 'other'},
])
def test_validation_is_atomic(tmp_path, changes):
    store = UniverseStore(tmp_path)
    with pytest.raises(CatalogError):
        store.create(data(**changes))
    assert store.list()['total'] == 0
    first = store.create(data())
    with pytest.raises(CatalogError):
        store.update(first['id'], {'revision': 1, **changes})
    assert store.export(first['id']) == first
    assert store.revisions(first['id']) == [first]


def test_idempotency_conflict_and_revision_conflicts(tmp_path):
    store = UniverseStore(tmp_path)
    payload = data()
    first = store.create(payload)
    assert store.create(payload) == first
    with pytest.raises(CatalogError) as conflict:
        store.create({**payload, 'title': 'Changed request'})
    assert conflict.value.status == 409
    second = store.update(first['id'], {'revision': 1, 'title': 'Changed'})
    with pytest.raises(CatalogError) as stale:
        store.update(first['id'], {'revision': 1, 'title': 'Stale'})
    assert stale.value.status == 409
    with pytest.raises(CatalogError):
        store.restore(first['id'], {'revision': 1, 'target_revision': 1})
    with pytest.raises(CatalogError):
        store.restore(first['id'], {'revision': 2, 'target_revision': 999})
    assert store.export(first['id']) == second
    assert len(store.revisions(first['id'])) == 2


def test_search_page_and_two_home_reference_rejection(tmp_path):
    store, _, _, ingredient, board, payload = linked(tmp_path / 'one')
    first = store.create(payload)
    for index in range(26):
        store.create(data(title=f'Second {index:02d}'))
    assert store.list()['total'] == 27
    assert len(store.list()['items']) == 25
    assert len(store.list(offset=25)['items']) == 2
    assert store.list(q='aster')['items'] == [first]
    assert store.list(q='no match')['items'] == []
    other = UniverseStore(tmp_path / 'two')
    with pytest.raises(CatalogError):
        other.get(first['id'])
    with pytest.raises(CatalogError):
        other.create(data(ingredient_ids=[ingredient['id']]))
    with pytest.raises(CatalogError):
        other.create(data(board_refs=[{'id': board['id'], 'revision': 1}]))
    assert other.list()['total'] == 0


def test_native_universe_operations_use_authoritative_state(tmp_path):
    async def run():
        provider = CreativeToolProvider(tmp_path)
        async def call(name, args):
            result = await provider.invoke('creative_universe_' + name, args)
            assert result.success, result.error
            return json.loads(result.output)
        first = await call('create', {'payload': data()})
        assert (await call('get', {'id': first['id']}))['canon'] == first['canon']
        edited = await call('update', {'id': first['id'], 'payload': {'revision': 1, 'title': 'Native edit'}})
        assert (await call('list', {'q': 'Native'}))['items'] == [edited]
        assert (await call('revisions', {'id': first['id'], 'limit': 1, 'offset': 1}))['items'] == [first]
        assert await call('export', {'id': first['id'], 'revision': 1}) == first
        restored = await call('restore', {'id': first['id'], 'payload': {'revision': 2, 'target_revision': 1}})
        assert UniverseStore(tmp_path).export(first['id']) == restored
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions['creative_universe_create'].requires_approval
        assert not definitions['creative_universe_export'].requires_approval
    asyncio.run(run())


def test_actual_http_create_edit_restore_export_and_scope_errors(tmp_path):
    async def run():
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(BASE, json=data())
            assert response.status == 201
            first = await response.json()
            id = first['id']
            changed = await client.patch(f'{BASE}/{id}', json={'revision': 1, 'title': 'HTTP edit'})
            assert changed.status == 200
            assert (await changed.json())['revision'] == 2
            stale = await client.patch(f'{BASE}/{id}', json={'revision': 1, 'title': 'Bad'})
            assert stale.status == 409
            listing = await client.get(BASE + '?q=HTTP&limit=1&offset=0')
            assert (await listing.json())['total'] == 1
            detail = await client.get(f'{BASE}/{id}')
            assert (await detail.json())['title'] == 'HTTP edit'
            exported = await client.get(f'{BASE}/{id}/export?revision=1')
            assert 'attachment' in exported.headers['Content-Disposition']
            assert await exported.json() == first
            restored = await client.post(f'{BASE}/{id}/restore', json={'revision': 2, 'target_revision': 1})
            assert restored.status == 200
            assert (await restored.json())['canon'] == first['canon']
            history = await client.get(f'{BASE}/{id}/revisions')
            assert [r['revision'] for r in (await history.json())['items']] == [3, 2, 1]
            for url in [BASE + '?home=other', BASE + '?offset=no', f'{BASE}/{id}/export?revision=0']:
                invalid = await client.get(url)
                assert invalid.status == 400
            missing = await client.get(BASE + '/missing')
            assert missing.status == 404
            malformed = await client.post(BASE, data='{bad', headers={'Content-Type': 'application/json'})
            assert malformed.status == 400
            override = await client.patch(f'{BASE}/{id}', json={'revision': 3, 'home': '/tmp/other'})
            assert override.status == 400
            assert UniverseStore(tmp_path).export(id)['revision'] == 3
    asyncio.run(run())
