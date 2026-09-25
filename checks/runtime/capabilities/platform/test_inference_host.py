import asyncio
import json
import socket
import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import ProviderRegistry, ProviderEntry, get_default_registry, set_default_registry
from gideon.workspace.capabilities.platform.inference_host import InferenceHost, HostError
from gideon.workspace.capabilities.platform.tools import create_provider
from gideon.interfaces.dashboard.handlers.capabilities_inference_host import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_ephemeral_secret

SECRET = 'local-qualification-peer-key-1234567890'
PREFIX = '/api/capabilities/platform/inference-host'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    previous = get_default_registry()
    registry = ProviderRegistry()
    registry.register_entry(ProviderEntry(name='Configured inference', type='uninstalled-direct-provider', model='configured-model'))
    set_default_registry(registry)
    CredentialStore(tmp_path).put('peer-key', {'type': 'static_token', 'value': SECRET})
    use_ephemeral_secret()
    yield tmp_path
    set_default_registry(previous)


def config(revision=0, **fields):
    return dict(revision=revision, provider='Configured inference', bind='127.0.0.1', port=0, capacity=1, peers={'peer-a': 'peer-key'}, **fields)


def url(host):
    return f'http://127.0.0.1:{host.port}'


def headers(secret=SECRET):
    return {'Authorization': 'Bearer ' + secret}


def test_unconfigured_state_does_not_claim_serving_or_hardware(home):
    host = InferenceHost(home)
    state = host.view()
    assert state['enabled'] is False
    assert state['listening'] is False
    assert state['actual_port'] is None
    assert state['usage'] == []
    assert state['active'] == 0
    assert state['queued'] == 0
    assert state['credentials'] == ['peer-key']
    assert state['providers'] == ['Configured inference']
    assert 'unavailable' in state['runtime_lifecycle']
    assert SECRET not in json.dumps(state)
    assert not host.path.exists()


@pytest.mark.asyncio
async def test_two_real_listeners_authenticate_discover_and_stop_independently(home):
    other = home / 'second-allocation'
    CredentialStore(other).put('peer-key', {'type': 'static_token', 'value': SECRET + '-other'})
    first, second = InferenceHost(home), InferenceHost(other)
    await first.configure(config())
    await second.configure(config())
    await first.start()
    await second.start()
    old_url = url(first)
    try:
        assert first.port != second.port
        async with aiohttp.ClientSession() as session:
            denied = await session.get(url(first) + '/v1/models')
            assert denied.status == 401
            models = await session.get(url(first) + '/v1/models', headers=headers())
            assert models.status == 200
            data = await models.json()
            assert data['data'][0]['id'] == 'configured-model'
            assert data['data'][0]['availability'] == 'not_probed'
            cross = await session.get(url(second) + '/v1/models', headers=headers())
            assert cross.status == 401
            accepted = await session.get(url(second) + '/v1/models', headers=headers(SECRET + '-other'))
            assert accepted.status == 200
            await first.stop()
            assert not first.state()['enabled']
            assert first.port is None
            with pytest.raises(aiohttp.ClientConnectorError):
                await session.get(old_url + '/v1/models', headers=headers())
            still_live = await session.get(url(second) + '/v1/models', headers=headers(SECRET + '-other'))
            assert still_live.status == 200
            assert second.state()['enabled']
    finally:
        await first.stop()
        await second.stop()
    assert not InferenceHost(home).state()['enabled']
    assert not InferenceHost(other).state()['enabled']


@pytest.mark.asyncio
async def test_rotation_and_revocation_take_effect_without_restart(home):
    host = InferenceHost(home)
    await host.configure(config())
    await host.start()
    original_port = host.port
    try:
        async with aiohttp.ClientSession() as session:
            assert (await session.get(url(host) + '/v1/models', headers=headers())).status == 200
            CredentialStore(home).put('peer-key', {'type': 'static_token', 'value': SECRET + '-rotated'})
            assert (await session.get(url(host) + '/v1/models', headers=headers())).status == 401
            assert (await session.get(url(host) + '/v1/models', headers=headers(SECRET + '-rotated'))).status == 200
            CredentialStore(home).remove('peer-key')
            assert (await session.get(url(host) + '/v1/models', headers=headers(SECRET + '-rotated'))).status == 401
            assert host.port == original_port
            assert host.state()['usage'] == []
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_provider_failure_is_real_resolution_failure_and_persisted_unknown_usage(home):
    host = InferenceHost(home)
    await host.configure(config())
    await host.start()
    try:
        async with aiohttp.ClientSession() as session:
            response = await session.post(url(host) + '/v1/chat/completions', headers=headers(), json={'model': 'configured-model', 'messages': [{'role': 'user', 'content': 'Private prompt never persisted'}]})
            assert response.status == 503
            assert await response.text() == 'Configured inference provider unavailable'
        row = host.state()['usage'][0]
        assert row['peer'] == 'peer-a'
        assert row['status'] == 'failed'
        assert row['provider'] == 'Configured inference'
        assert row['model'] == 'configured-model'
        assert row['input_tokens'] is None
        assert row['output_tokens'] is None
        assert row['duration_ms'] >= 0
        assert host.view()['active'] == 0
        assert 'Private prompt' not in host.path.read_text()
        assert SECRET not in host.path.read_text()
        assert not (home / 'usage/turns.jsonl').exists()
    finally:
        await host.stop()
    assert InferenceHost(home).state()['usage'] == [row]


@pytest.mark.asyncio
async def test_restart_arm_disarm_idempotency_and_stale_configuration(home):
    host = InferenceHost(home)
    configured = await host.configure(config())
    assert configured['revision'] == 1
    with pytest.raises(HostError, match='changed'):
        await host.configure(config())
    active = await host.start()
    again = await host.start()
    assert active['actual_port'] == again['actual_port']
    assert active['revision'] == again['revision']
    with pytest.raises(HostError, match='Disarm'):
        await host.configure(config(active['revision']))
    await host.stop(disarm=False)
    assert host.state()['enabled']
    restarted = InferenceHost(home)
    await restarted.start()
    try:
        assert restarted.view()['listening']
        assert restarted.view()['enabled']
        assert restarted.view()['actual_port'] is not None
    finally:
        await restarted.stop()
    stopped = restarted.state()
    await restarted.stop()
    assert restarted.state()['revision'] == stopped['revision']
    assert not restarted.state()['enabled']
    assert restarted.state()['peers'] == {'peer-a': 'peer-key'}


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('bind','0.0.0.0'),('bind','8.8.8.8'),('capacity',0),('capacity',9),('port',-1),('port',70000),('peers',{}),('peers',{'peer':'missing'})])
async def test_invalid_host_configuration_never_arms(home, field, value):
    host = InferenceHost(home)
    body = config()
    body[field] = value
    with pytest.raises((ValueError, KeyError)):
        await host.configure(body)
    assert not host.state()['enabled']
    assert not host.path.exists()
    assert host.runner is None


@pytest.mark.asyncio
async def test_bind_collision_leaves_no_new_listener_or_enabled_marker(home):
    host = InferenceHost(home)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',0))
        occupied.listen()
        body = config()
        body['port'] = occupied.getsockname()[1]
        await host.configure(body)
        with pytest.raises(OSError):
            await host.start()
        assert not host.state()['enabled']
        assert host.runner is None
        assert host.port is None


@pytest.mark.asyncio
async def test_request_validation_rejects_stream_tools_model_switch_and_bad_messages(home):
    host = InferenceHost(home)
    await host.configure(config())
    await host.start()
    valid = {'model': 'configured-model', 'messages': [{'role': 'user', 'content': 'Hello'}]}
    try:
        async with aiohttp.ClientSession() as session:
            for payload in [{**valid,'stream':True},{**valid,'model':'another'},{**valid,'tools':[]},{**valid,'messages':[]},{**valid,'messages':[{'role':'tool','content':'data'}]}]:
                response = await session.post(url(host) + '/v1/chat/completions', headers=headers(), json=payload)
                assert response.status == 400
            assert host.state()['usage'] == []
            assert host.view()['active'] == 0
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_dashboard_and_native_control_actual_listener_and_cleanup(home):
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    register(app)
    auth = {'Cookie':'gideon_token_8000=' + generate_token('host-owner')}
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status == 403
        configured = await client.post(PREFIX, headers=auth, json={'action':'configure','config':config()})
        assert configured.status == 200
        started = await client.post(PREFIX, headers=auth, json={'action':'start'})
        assert started.status == 200
        data = await started.json()
        assert data['listening']
        assert data['enabled']
        remote = f"http://127.0.0.1:{data['actual_port']}/v1/models"
        async with aiohttp.ClientSession() as session:
            assert (await session.get(remote,headers=headers())).status == 200
        tool = await create_provider().invoke('platform_inference_host',{})
        assert tool.success
        assert json.loads(tool.output)['actual_port'] == data['actual_port']
        assert SECRET not in tool.output
        stopped = await client.post(PREFIX, headers=auth, json={'action':'stop'})
        assert stopped.status == 200
        assert not (await stopped.json())['enabled']
        assert (await client.post(PREFIX,headers=auth,json={'action':'bad'})).status == 400
    assert not InferenceHost(home).state()['enabled']
