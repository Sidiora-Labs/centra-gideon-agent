import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.extensions.apps.backend_runtime import get_backend_supervisor
from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore, Conflict, NotFound
from gideon.workspace.capabilities.experience.world_engine import WorldEngine, APP_ID, PREFIX
from gideon.workspace.capabilities.experience.worlds import Worlds, identifier, get_worlds
from gideon.workspace.capabilities.experience.tools import ExperienceTools
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementStore
from test_world_engine import DEPS, install_engine, receive_type


@pytest.fixture
async def world(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.setenv('PATH', str(DEPS / 'bun-linux-x64') + os.pathsep + os.environ['PATH'])
    store = ExperienceStore(tmp_path)
    engine = WorldEngine(store)
    install_engine()
    assert (await engine.control('start', {}))['state'] == 'running'
    service = get_worlds(store)
    yield service
    await service.close()
    get_backend_supervisor().stop(APP_ID)


@pytest.mark.asyncio
async def test_real_canonical_objects_revisions_receipts_restart(world):
    with pytest.raises(NotFound):
        await world.call('workshop')
    snapshot = await world.open('workshop', {})
    assert snapshot['world'] == 'workshop'
    assert snapshot['seq'] >= 1
    assert snapshot['state']['entities'] == {}
    assert any(person['id'] == 'gideon' for person in snapshot['present'])
    assert all(set(person) <= {'id','avatar','pose','agent'} for person in snapshot['present'])
    assert await world.open('workshop', {}) == snapshot
    create = {'operation':'spawn','id':'desk','position':[1,0,2],'expected_seq':snapshot['seq'],'request_id':'create_desk'}
    receipt = await world.mutate('workshop', 'objects', create)
    assert receipt['complete'] is True
    assert len(receipt['operations']) == 1
    assert receipt['operations'][0]['verb'] == 'spawn'
    after = await world.call('workshop')
    assert after['state']['entities']['desk']['pos'] == [1,0,2]
    assert after['seq'] == receipt['seq']
    assert await world.mutate('workshop','objects',create) == receipt
    assert (await world.call('workshop'))['seq'] == after['seq']
    with pytest.raises(Conflict, match='reused'):
        await world.mutate('workshop','objects',{**create,'position':[2,0,0]})
    with pytest.raises(Conflict, match='changed'):
        await world.mutate('workshop','objects',{**create,'request_id':'stale','id':'second'})
    with pytest.raises(Conflict, match='exists'):
        await world.mutate('workshop','objects',{**create,'request_id':'duplicate','expected_seq':after['seq']})
    placed = await world.mutate('workshop','objects',{'operation':'place','id':'desk','position':[5,1,5],'expected_seq':after['seq'],'request_id':'move'})
    assert placed['complete']
    moved = await world.call('workshop')
    assert moved['state']['entities']['desk']['pos'] == [5,1,5]
    await world.close()
    assert world.connections == {}
    assert (await world.engine.control('stop', {}))['state'] == 'stopped'
    assert (await world.engine.control('start', {}))['state'] == 'running'
    restored = await world.open('workshop', {})
    assert restored['state']['entities']['desk']['pos'] == [5,1,5]
    assert await world.mutate('workshop','objects',create) == receipt
    removed = await world.mutate('workshop','objects',{'operation':'remove','id':'desk','expected_seq':restored['seq'],'request_id':'remove'})
    assert removed['complete']
    assert 'desk' not in (await world.call('workshop'))['state']['entities']
    with pytest.raises(Conflict, match='does not exist'):
        await world.mutate('workshop','objects',{'operation':'remove','id':'absent','expected_seq':removed['seq'],'request_id':'missing'})


@pytest.mark.asyncio
async def test_projection_reads_real_sources_preserves_edit_and_retries(world):
    goals = GoalStore(world.engine.home / 'capabilities/identity/goals.sqlite3')
    goal = goals.save_goal(title='Build a garden', request_id='garden')
    preview = await world.sources()
    selected = [row for row in preview['sources'] if row['kind']=='goals']
    assert [row['title'] for row in selected] == ['Build a garden']
    assert selected[0]['id'] == goal['id']
    assert selected[0]['status'] == 'active'
    assert selected[0]['url'] == '#/capabilities/identity'
    assert 'memory' in preview['unavailable']
    snapshot = await world.open('garden', {})
    body = {'kinds':['goals'],'expected_seq':snapshot['seq'],'request_id':'project_garden'}
    receipt = await world.mutate('garden','project',body)
    assert receipt['complete']
    assert [row['verb'] for row in receipt['operations']] == ['spawn','comp']
    actual = await world.call('garden')
    assert len(actual['state']['entities']) == 1
    key, entity = next(iter(actual['state']['entities'].items()))
    assert entity['comp']['gideon_source'] == selected[0]
    assert await world.mutate('garden','project',body) == receipt
    move = await world.mutate('garden','objects',{'operation':'place','id':key,'position':[10,2,-1],'expected_seq':actual['seq'],'request_id':'arrange'})
    goals.save_goal(id=goal['id'], expected_revision=goal['revision'], title='Garden completed', status='completed', request_id='update_garden')
    refreshed = await world.mutate('garden','project',{'kinds':['goals'],'expected_seq':move['seq'],'request_id':'refresh_garden'})
    assert [row['verb'] for row in refreshed['operations']] == ['comp']
    final = await world.call('garden')
    assert len(final['state']['entities']) == 1
    assert final['state']['entities'][key]['pos'] == [10,2,-1]
    assert final['state']['entities'][key]['comp']['gideon_source']['title'] == 'Garden completed'
    assert final['state']['entities'][key]['comp']['gideon_source']['status'] == 'completed'
    assert goals.get_goal(goal['id'])['title'] == 'Garden completed'
    assert not any(row.get('kind') == 'health' for row in final['state']['entities'].values())


@pytest.mark.asyncio
async def test_real_http_world_presence_disconnect_and_native_tools(world):
    app = web.Application()
    app[STORE] = world.store
    register(app)
    async with TestClient(TestServer(app)) as client:
        base = '/api/capabilities/experience/worlds/lounge'
        assert (await client.get(base)).status == 404
        opened = await client.post(base + '/open', json={})
        assert opened.status == 200
        snapshot = await opened.json()
        async with client.ws_connect(PREFIX + '/host/ws') as visitor:
            await visitor.send_json({'type':'join','world':'lounge','id':'alice','avatar':'eidoverse/assets/vrms/claude.vrm'})
            await receive_type(visitor,'snapshot')
            await visitor.send_json({'type':'pose','pose':{'p':[3,0,3],'yaw':1,'speed':0,'clip':'idle'}})
            for _ in range(30):
                present = (await (await client.get(base)).json())['present']
                if any(row['id']=='alice' for row in present):
                    break
                await asyncio.sleep(.01)
            assert {row['id'] for row in present} == {'gideon','alice'}
            assert all('auth' not in row and 'sub' not in row for row in present)
        for _ in range(30):
            state = await (await client.get(base)).json()
            if all(row['id'] != 'alice' for row in state['present']):
                break
            await asyncio.sleep(.01)
        assert [row['id'] for row in state['present']] == ['gideon']
        source_response = await client.get(base + '/sources')
        assert source_response.status == 200
        assert isinstance((await source_response.json())['sources'], list)
        for body in [[], False, {'home':'/root'}, {'world':'other'}]:
            assert (await client.post(base + '/open', json=body)).status == 400
        assert (await client.post(base + '/objects', json={'operation':'exec'})).status == 400
        result = await client.post(base + '/objects', json={'operation':'spawn','id':'table','position':[0,0,0],'expected_seq':state['seq'],'request_id':'table'})
        assert result.status == 200
        assert (await result.json())['complete']
        current = await (await client.get(base)).json()
        assert 'table' in current['state']['entities']
        native = ExperienceTools(world.store)
        connection = world.connections['lounge'][1]
        result = await native.invoke('experience_world_objects', {'world':'lounge','body':{'operation':'place','id':'table','position':[2,0,2],'expected_seq':current['seq'],'request_id':'interleaved'}})
        assert result.success
        assert not connection.closed
        assert world.connections['lounge'][1] is connection
        current = await (await client.get(base)).json()
        assert current['state']['entities']['table']['pos'] == [2,0,2]
        assert [row['id'] for row in current['present']] == ['gideon']
        result = await client.post(base + '/objects', json={'operation':'place','id':'table','position':[3,0,3],'expected_seq':current['seq'],'request_id':'after_native'})
        assert result.status == 200
        assert (await result.json())['complete']
    provider = ExperienceTools(world.store)
    result = await provider.invoke('experience_world_get', {'world':'lounge'})
    assert result.success
    canonical = json.loads(result.output)
    assert 'table' in canonical['state']['entities']
    definitions = {row.name:row for row in await provider.list_tools()}
    assert not definitions['experience_world_get'].requires_approval
    assert definitions['experience_world_objects'].requires_approval
    result = await provider.invoke('experience_world_objects', {'world':'lounge','body':{'operation':'place','id':'table','position':[4,0,4],'expected_seq':canonical['seq'],'request_id':'native_move'}})
    assert result.success
    assert json.loads(result.output)['complete']
    assert (await world.call('lounge'))['state']['entities']['table']['pos'] == [4,0,4]
    result = await provider.invoke('experience_world_get', {'world':'does_not_exist'})
    assert not result.success


@pytest.mark.asyncio
async def test_validation_scope_and_absence_are_not_world_success(world, monkeypatch, tmp_path):
    for value in ['../escape','a/b','a b','',None,[], 'a'*65]:
        with pytest.raises(ValueError):
            identifier(value)
    snapshot = await world.open('validation', {})
    base = {'operation':'spawn','id':'safe','position':[0,0,0],'expected_seq':snapshot['seq'],'request_id':'safe'}
    invalid = [None, [], False, {**base,'home':'other'}, {**base,'expected_seq':True}, {**base,'expected_seq':-2}, {**base,'request_id':'../x'}, {**base,'id':'/x'}, {**base,'operation':[]}, {**base,'position':[0,0]}, {**base,'position':[float('nan'),0,0]}, {**base,'position':[True,0,0]}, {**base,'position':[10001,0,0]}]
    for body in invalid:
        with pytest.raises(ValueError):
            await world.mutate('validation','objects',body)
    for kinds in [[], {}, ['unknown'], ['health','health'], [[]]]:
        with pytest.raises(ValueError):
            await world.mutate('validation','project',{'kinds':kinds,'expected_seq':snapshot['seq'],'request_id':'invalid'})
    assert (await world.call('validation'))['state']['entities'] == {}
    another = tmp_path / 'uncreated'
    monkeypatch.setenv('GIDEON_HOME', str(another))
    with pytest.raises(Conflict):
        await world.call('validation')
    with pytest.raises(Conflict):
        await world.sources()
    assert not another.exists()
