import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.workspace.capabilities.wellbeing.epigenetic import EpigeneticStore
from gideon.workspace.capabilities.wellbeing.epigenetic_http import register
from gideon.workspace.capabilities.wellbeing.epigenetic_provider import EpigeneticProvider, create_provider


BASE = '/api/capabilities/wellbeing/epigenetic'


def payload(**changes):
    value = {
        'request_id': 'epi-create-1', 'source_report_id': 'TruAge-2026-09', 'observed_at': '2026-09-20',
        'source': 'Owner-uploaded TruAge report', 'biological_age': {'value': 38.4, 'unit': 'years'},
        'chronological_age': {'value': 41, 'unit': 'years'}, 'pace_of_aging': {'value': .91, 'scale': 'years/year'},
        'organ_scores': {'heart': {'value': 36.2, 'unit': 'years'}, 'liver': {'value': 72, 'scale': 'percentile'}, 'brain': None},
        'notes': 'Values transcribed from source report',
    }
    value.update(changes)
    return value


async def client(home, identity):
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app, home)
    client = TestClient(TestServer(app)); await client.start_server()
    assert (await client.get(BASE)).status in {401, 403}
    response = await client.get(BASE, params={'token': generate_token(identity)})
    assert response.status == 200
    return client


async def call(client, method, path, payload=None):
    response = await client.request(method, path, json=payload)
    return response.status, await response.json()


def test_create_correct_history_export_reopen_and_home_isolation(tmp_path):
    async def journey():
        use_ephemeral_secret()
        first, other = await client(tmp_path / 'a', 'epigenetic-a'), await client(tmp_path / 'b', 'epigenetic-b')
        try:
            status, created = await call(first, 'POST', BASE, payload())
            assert status == 201
            assert created['evidence_basis'] == 'source_reported' and created['revision'] == 1
            assert created['biological_age'] == {'value': 38.4, 'unit': 'years'}
            assert created['pace_of_aging'] == {'value': .91, 'scale': 'years/year'}
            assert created['organ_scores']['brain'] is None
            status, replay = await call(first, 'POST', BASE, payload())
            assert status == 201 and replay == created
            status, hidden = await call(other, 'GET', BASE)
            assert status == 200 and hidden == {'records': []}
            correction = {'request_id':'epi-correct-1','revision':1,'biological_age':{'value':37.9,'unit':'years'},'notes':'Corrected transcription'}
            status, corrected = await call(first, 'PUT', BASE + '/' + created['id'], correction)
            assert status == 200 and corrected['revision'] == 2 and corrected['source'] == created['source']
            assert corrected['organ_scores'] == created['organ_scores'] and corrected['biological_age']['value'] == 37.9
            status, conflict = await call(first, 'PUT', BASE + '/' + created['id'], {**correction,'request_id':'stale'})
            assert status == 409 and conflict['code'] == 'conflict'
            status, history = await call(first, 'GET', BASE + '/' + created['id'] + '/history')
            assert status == 200 and history['history'] == [created, corrected]
            status, exported = await call(first, 'GET', BASE + '/export')
            assert status == 200 and exported['record_family'] == 'source_reported_epigenetic_results'
            assert exported['records'] == [corrected] and exported['history'] == [created, corrected]
            await first.close(); first = await client(tmp_path / 'a', 'epigenetic-reopen')
            status, reopened = await call(first, 'GET', BASE + '/' + created['id'])
            assert status == 200 and reopened == corrected
        finally:
            await first.close(); await other.close()
    asyncio.run(journey())


@pytest.mark.parametrize('bad', [
    payload(observed_at='09/20/2026'), payload(biological_age=38),
    payload(pace_of_aging={'value': .9, 'unit':'ratio', 'scale':'years/year'}),
    payload(organ_scores={'heart': {'value': float('inf'), 'unit':'years'}}),
    payload(organ_scores={'heart': {'value': 30}}), payload(diagnosis='younger'),
])
def test_invalid_or_diagnostic_fields_are_atomic(tmp_path, bad):
    store = EpigeneticStore(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        store.create(bad)
    assert store.list() == []


@pytest.mark.asyncio
async def test_native_tools_use_real_store_and_require_approval_only_for_mutations(tmp_path):
    provider = EpigeneticProvider(tmp_path)
    tools = {tool.name: tool for tool in await provider.list_tools()}
    assert set(tools) == {'wellbeing_epigenetic_list','wellbeing_epigenetic_get','wellbeing_epigenetic_history','wellbeing_epigenetic_export','wellbeing_epigenetic_create','wellbeing_epigenetic_correct'}
    assert tools['wellbeing_epigenetic_create'].requires_approval and tools['wellbeing_epigenetic_correct'].requires_approval
    assert not tools['wellbeing_epigenetic_list'].requires_approval and not tools['wellbeing_epigenetic_export'].requires_approval
    result = await provider.invoke('wellbeing_epigenetic_create', {'payload':payload(request_id='native-create')})
    assert result.success
    created = json.loads(result.output)
    result = await provider.invoke('wellbeing_epigenetic_correct', {'id':created['id'],'payload':{'request_id':'native-correct','revision':1,'pace_of_aging':None}})
    assert result.success and json.loads(result.output)['pace_of_aging'] is None
    history = await provider.invoke('wellbeing_epigenetic_history', {'id':created['id']})
    assert len(json.loads(history.output)) == 2
    assert EpigeneticStore(tmp_path).get(created['id'])['revision'] == 2
    unknown = await provider.invoke('wellbeing_epigenetic_diagnose', {'id':created['id']})
    assert not unknown.success
    with pytest.raises(ValueError, match='does not accept caller'):
        create_provider({'home':'/tmp/escape'})
