import asyncio
import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.config.loader import config_path
from gideon.engine.agents import runners
from gideon.integrations.llm.registry import ProviderEntry, ProviderRegistry, get_default_registry, set_default_registry
from gideon.interfaces.dashboard.handlers.capabilities_harnesses import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.workspace.capabilities.platform.harnesses import inventory
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/harnesses'


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    previous = get_default_registry()
    set_default_registry(ProviderRegistry())
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text('{"providers":[]}')
    yield
    set_default_registry(previous)


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    return app


async def authenticate(client):
    response = await client.get(PREFIX, params={'token': generate_token('harness-owner')})
    assert response.status == 200
    return await response.json()


def codex(data):
    return next(row for row in data['harnesses'] if row['id'] == 'codex')


@pytest.mark.asyncio
async def test_actual_package_install_handshake_update_remove_and_provenance():
    async with TestClient(TestServer(application())) as client:
        initial = await authenticate(client)
        assert codex(initial)['installed'] is None
        response = await client.post(PREFIX + '/codex', json={'action': 'install', 'version': '1.13.0'})
        assert response.status == 200, await response.text()
        installed = codex(await response.json())
        assert installed['installed']['version'] == '1.13.0'
        assert installed['installed']['integrity'].startswith('sha512-')
        assert installed['authentication'] == 'unchecked'
        ledger = json.loads(runners.adapter_lock_path().read_text())
        assert ledger[installed['package']]['version'] == '1.13.0'
        assert ledger[installed['package']]['integrity'] == installed['installed']['integrity']
        binary = runners.managed_adapter_prefix() / 'node_modules/.bin/codex-acp'
        assert binary.exists()
        process = await asyncio.create_subprocess_exec(str(binary), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            message = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': 1, 'clientCapabilities': {}, 'clientInfo': {'name': 'gideon-lifecycle-check', 'version': '1'}}}
            process.stdin.write((json.dumps(message) + '\n').encode())
            await process.stdin.drain()
            raw = await asyncio.wait_for(process.stdout.readline(), 30)
            assert raw, 'Real installed ACP adapter returned no initialize response'
            reply = json.loads(raw)
            assert reply['id'] == 1
            assert 'result' in reply, reply
            assert reply['result']['protocolVersion'] == 1
        finally:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 10)
        stale = await client.post(PREFIX + '/codex', json={'action': 'update', 'version': '1.13.1', 'expected_version': '0.0.0'})
        assert stale.status == 409
        assert 'refresh' in (await stale.json())['error']
        assert codex(inventory())['installed']['version'] == '1.13.0'
        updated = await client.post(PREFIX + '/codex', json={'action': 'update', 'version': '1.13.1', 'expected_version': '1.13.0'})
        assert updated.status == 200, await updated.text()
        assert codex(await updated.json())['installed']['version'] == '1.13.1'
        assert json.loads(runners.adapter_lock_path().read_text())[installed['package']]['version'] == '1.13.1'
        get_default_registry().register_entry(ProviderEntry(name='acp:codex', type='acp_agent', model='', options={'command': [str(binary)]}))
        blocked = await client.post(PREFIX + '/codex', json={'action': 'remove', 'expected_version': '1.13.1'})
        assert blocked.status == 409
        assert 'dependent' in (await blocked.json())['error']
        assert binary.exists()
        get_default_registry().unregister_entry('acp:codex')
        removed = await client.post(PREFIX + '/codex', json={'action': 'remove', 'expected_version': '1.13.1'})
        assert removed.status == 200, await removed.text()
        assert codex(await removed.json())['installed'] is None
        assert not binary.exists()
        assert installed['package'] not in json.loads(runners.adapter_lock_path().read_text())
        assert runners.managed_adapter_prefix().parent == config_path().parent


@pytest.mark.asyncio
@pytest.mark.parametrize('identifier,body,status,text', [
    ('unknown', {'action': 'install', 'version': '1.0.0'}, 404, 'Unknown'),
    ('gemini-cli', {'action': 'install', 'version': '1.0.0'}, 409, 'no managed'),
    ('codex', {'action': 'install', 'version': 'latest'}, 400, 'exact'),
    ('codex', {'action': 'install', 'version': '1.0.0; touch hacked'}, 400, 'exact'),
    ('codex', {'action': 'install', 'version': '../package'}, 400, 'exact'),
    ('codex', {'action': 'arbitrary'}, 400, 'Expected'),
    ('codex', {'action': 'remove', 'expected_version': '1.13.1'}, 409, 'refresh'),
    ('codex', {'action': 'remove'}, 409, 'installed state'),
    ('codex', {'action': 'update', 'version': '1.13.1'}, 409, 'installed state'),
    ('codex', {'action': 'install', 'version': '1.13.1', 'package': 'untrusted'}, 400, 'Invalid'),
    ('codex', [], 400, 'Invalid'),
])
async def test_invalid_operations_never_mutate_adapter_directory(identifier, body, status, text):
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        response = await client.post(PREFIX + '/' + identifier, json=body)
        assert response.status == status
        assert text in (await response.json())['error']
        assert not runners.managed_adapter_prefix().exists()
        assert codex(inventory())['installed'] is None


@pytest.mark.asyncio
async def test_auth_scope_catalog_truth_and_real_native_inventory():
    async with TestClient(TestServer(application())) as client:
        denied = await client.get(PREFIX)
        assert denied.status in {401, 403}
        app = await client.get(PREFIX, params={'token': generate_token('owner', app='external')})
        assert app.status in {401, 403}
    async with TestClient(TestServer(application())) as client:
        data = await authenticate(client)
        assert data['version'] == 1
        assert set(row['id'] for row in data['harnesses']) == set(runners.catalog())
        assert all(row['latest'] is None for row in data['harnesses'])
        assert all(row['authentication'] == 'unchecked' for row in data['harnesses'])
        assert all(not row['dependents'] for row in data['harnesses'])
        assert 'node_modules' not in json.dumps(data)
        provider = create_provider()
        definitions = await provider.list_tools()
        tool = next(tool for tool in definitions if tool.name == 'platform_harness_inventory')
        assert tool.requires_approval is False
        result = await provider.invoke('platform_harness_inventory', {})
        assert result.success
        assert json.loads(result.output) == data
        invalid = await provider.invoke('platform_harness_inventory', {'command': 'install'})
        assert not invalid.success
        manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
        assert tool.name in manifest['provider']['capabilities']
        assert not runners.managed_adapter_prefix().exists()
