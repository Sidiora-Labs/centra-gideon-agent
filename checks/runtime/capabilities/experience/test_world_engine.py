import asyncio
import json
import os
import subprocess
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.extensions.apps import app_manager
from gideon.extensions.apps.backend_runtime import BackendSupervisor, get_backend_supervisor
from gideon.extensions.apps.manager import app_data_dir, _read_installed
from gideon.interfaces.dashboard.handlers.capabilities_experience import register, STORE
from gideon.workspace.capabilities.experience import ExperienceStore, Conflict
from gideon.workspace.capabilities.experience.world_engine import WorldEngine, APP_ID, PREFIX
from gideon.workspace.capabilities.experience.world_engine_http import rewrite_text
from gideon.workspace.capabilities.experience.tools import ExperienceTools

DEPS = Path('/tmp/gideon-world-engine-deps')
TEMPLATE = Path(__file__).resolve().parents[4] / 'runtime/gideon/workspace/capabilities/experience/assets/world-engine'


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.setenv('PATH', str(DEPS / 'bun-linux-x64') + os.pathsep + os.environ['PATH'])
    service = WorldEngine(ExperienceStore(tmp_path))
    yield service
    get_backend_supervisor().stop(APP_ID)


def install_engine():
    result = app_manager.install(TEMPLATE, confirm=True)
    assert result.ok, result.error
    get_backend_supervisor().stop(APP_ID)
    data = app_data_dir(APP_ID)
    (data / 'engine.json').write_text(json.dumps({'worlds': str(DEPS / 'worlds'), 'video': str(DEPS / 'video')}))
    return data


async def receive_type(socket, kind):
    for _ in range(100):
        message = await asyncio.wait_for(socket.receive_json(), timeout=10)
        if message.get('type') == kind:
            return message
    raise AssertionError('Expected world protocol message was not received: ' + kind)


@pytest.mark.asyncio
async def test_uninstalled_engine_and_strict_operator_boundary(engine, monkeypatch, tmp_path):
    before = await engine.status()
    assert before['state'] == 'unavailable'
    assert before['version'] is None
    assert before['engine_url'] is None
    assert 'Operator must install' in before['reason']
    assert await engine.control('start', {}) == before
    for body in [None, [], False, {'worlds': '/other'}, {'command': 'echo unsafe'}, {'home': '/other'}]:
        with pytest.raises(ValueError):
            await engine.control('start', body)
    with pytest.raises(Conflict):
        engine.target()
    another = tmp_path / 'uncreated'
    monkeypatch.setenv('GIDEON_HOME', str(another))
    with pytest.raises(Conflict):
        await engine.status()
    assert not another.exists()


@pytest.mark.asyncio
async def test_real_engine_app_lifecycle_http_websocket_and_persistence(engine):
    data = install_engine()
    assert _read_installed(APP_ID).enabled
    state = await engine.control('start', {})
    assert state['state'] == 'running', state
    assert isinstance(state['version'], dict)
    assert state['engine_url'] == PREFIX + '/host/'
    backend = get_backend_supervisor().get(APP_ID)
    assert backend is not None and backend.is_alive()
    pid = backend.pid
    assert (await engine.control('start', {}))['state'] == 'running'
    assert get_backend_supervisor().get(APP_ID).pid == pid
    async with aiohttp.ClientSession() as direct:
        async with direct.get(backend.base_url + '/version') as denied:
            assert denied.status == 403
        async with direct.get(backend.base_url + '/version', headers={'X-Gideon-Proxy': '1:' + '0' * 64}) as denied:
            assert denied.status == 403
        async with direct.get(backend.base_url + '/health') as health:
            assert health.status == 200
    app = web.Application()
    app[STORE] = ExperienceStore(engine.home)
    register(app)
    async with TestClient(TestServer(app)) as client:
        version = await client.get(PREFIX + '/host/version', headers={'Authorization': 'untrusted-owner-token', 'X-Gideon-Proxy': 'bad-caller-signature'})
        assert version.status == 200
        assert (await version.json()) == state['version']
        page = await client.get(PREFIX + '/host/')
        assert page.status == 200
        html = await page.text()
        assert '<base href="' + PREFIX + '/host/">' in html
        assert 'bridgeURL' in html
        assert 'untrusted-owner-token' not in html
        script = await client.get(PREFIX + '/host/lib/net.js')
        assert script.status == 200
        assert 'WebSocket' in await script.text()
        socket = await client.ws_connect(PREFIX + '/host/ws')
        await socket.send_json({'type': 'join', 'world': 'gideon-check', 'id': 'alice', 'avatar': 'eidoverse/assets/vrms/claude.vrm'})
        snapshot = await receive_type(socket, 'snapshot')
        assert snapshot['you'] == 'alice'
        assert [entry['verb'] for entry in snapshot['entries']] == ['genesis', 'grant']
        assert snapshot['entries'][1]['args']['role'] == 'owner'
        await socket.send_json({'type': 'verb', 'verb': 'say', 'args': {'text': 'Persistent actual world message'}})
        event = await receive_type(socket, 'log')
        assert 'Persistent actual world message' in json.dumps(event)
        await socket.close()
        assert socket.closed
        stopped = await client.post(PREFIX + '/stop', json={})
        assert (await stopped.json())['state'] == 'stopped'
        assert get_backend_supervisor().get(APP_ID) is None
        denied = await client.get(PREFIX + '/host/version')
        assert denied.status == 409
        restarted = await client.post(PREFIX + '/start', json={})
        assert (await restarted.json())['state'] == 'running'
        async with client.ws_connect(PREFIX + '/host/ws') as again:
            await again.send_json({'type': 'join', 'world': 'gideon-check', 'id': 'alice', 'avatar': 'eidoverse/assets/vrms/claude.vrm'})
            restored = await receive_type(again, 'snapshot')
            assert 'Persistent actual world message' in json.dumps(restored)
        assert any((data / 'worlds').rglob('*.jsonl'))
        assert (await client.post(PREFIX + '/start', json={'port': 1234})).status == 400
        assert (await client.get(PREFIX)).status == 200
    provider = ExperienceTools(ExperienceStore(engine.home))
    result = await provider.invoke('experience_world_engine_get', {})
    assert result.success
    assert json.loads(result.output)['state'] == 'running'
    result = await provider.invoke('experience_world_engine_stop', {})
    assert result.success
    assert json.loads(result.output)['state'] == 'stopped'
    definitions = {tool.name: tool for tool in await provider.list_tools()}
    assert definitions['experience_world_engine_start'].requires_approval
    assert definitions['experience_world_engine_stop'].requires_approval
    assert not definitions['experience_world_engine_get'].requires_approval


@pytest.mark.asyncio
async def test_missing_operator_configuration_cannot_claim_running(engine):
    installed = app_manager.install(TEMPLATE, confirm=True)
    assert installed.ok, installed.error
    get_backend_supervisor().stop(APP_ID)
    status = await engine.status()
    assert status['state'] == 'unavailable'
    assert 'configuration is missing' in status['reason']
    assert status['version'] is None
    assert status['engine_url'] is None
    assert (await engine.control('start', {}))['state'] != 'running'


def test_actual_pinned_dependencies_and_bun_launch_contract():
    pins = {'worlds': 'bf9e0231795d55297f976948e2a79de3e1320e9d', 'video': '2d152b9ae4e12e9ec152ea088850e377c319d4d4'}
    for name, expected in pins.items():
        actual = subprocess.check_output(['git', '-C', str(DEPS / name), 'rev-parse', 'HEAD'], text=True).strip()
        assert actual == expected
        assert (DEPS / name / 'LICENSE').is_file()
    bun = subprocess.check_output([str(DEPS / 'bun-linux-x64/bun'), '--version'], text=True).strip()
    assert bun == '1.4.2'
    assert BackendSupervisor._launch_cmd('bun', Path('/app/launch.js')) == ['bun', '/app/launch.js']
    assert BackendSupervisor._launch_cmd('node', Path('/app/launch.js')) == ['node', '/app/launch.js']


def test_real_engine_asset_rewrite_vectors_preserve_external_sources():
    source = (DEPS / 'worlds/client/index.html').read_text()
    rewritten = rewrite_text(source, 'text/html')
    assert rewritten.count('<base href=') == 1
    assert 'bridgeURL' in rewritten
    assert '/api/capabilities/experience/world-engine/host/' in rewritten
    module = (DEPS / 'worlds/client/lib/net.js').read_text()
    routed = rewrite_text(module, 'application/javascript')
    assert 'new WebSocket' in routed
    external = 'import value from "https://example.com/module.js"; const asset="/library/a.glb";'
    mapped = rewrite_text(external, 'application/javascript')
    assert '"https://example.com/module.js"' in mapped
    assert '"' + PREFIX + '/host/library/a.glb"' in mapped


@pytest.mark.asyncio
async def test_actual_launcher_refuses_wrong_pinned_dependency(engine):
    data = install_engine()
    (data / 'engine.json').write_text(json.dumps({'worlds': str(DEPS / 'video'), 'video': str(DEPS / 'video')}))
    result = await engine.control('start', {})
    assert result['state'] != 'running'
    assert result['version'] is None
    assert result['engine_url'] is None
    await asyncio.sleep(0.1)
    assert get_backend_supervisor().get(APP_ID) is None
    assert not (data / 'worlds').exists()
    assert not (data / 'assets').exists()
    assert not (data / 'relay').exists()
    stopped = await engine.control('stop', {})
    assert stopped['state'] == 'stopped'
    assert not _read_installed(APP_ID).enabled
