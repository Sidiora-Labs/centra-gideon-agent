"""Pinned use-case model Tests through the real registered local provider and HTTP API."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import web, ClientSession
from gideon.integrations.llm.registry import get_default_registry, sync_entries_from_config
from gideon.interfaces.dashboard.handlers.model_registry import api_model_test
from gideon.extensions.providers import model_test


@pytest.mark.asyncio
async def test_real_local_model_test_success_refusal_timeout_strict_candidate_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_AGENT_ID', raising=False)
    monkeypatch.delenv('GIDEON_SESSION_KEY', raising=False)
    registry = get_default_registry(); original = dict(registry._entries); registry._entries.clear()
    source = Path(__file__).parents[2] / 'runtime/gideon/extensions/apps/native/ollama-models/provider.py'
    spec = importlib.util.spec_from_file_location('model_test_contract_ollama', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    requests = []
    entered = asyncio.Event(); finish = asyncio.Event()
    async def provider(request):
        if request.path == '/api/tags': return web.json_response({'models': []})
        if request.path == '/api/ps': return web.json_response({'models': []})
        if request.path == '/api/show': return web.json_response({'model_info': {'test.context_length': 8192}, 'capabilities': ['completion']})
        body = await request.json(); requests.append(body)
        if body['model'].startswith('refused'):
            return web.json_response({'error': 'private-provider-message'}, status=401)
        if body['model'].startswith('slow'):
            entered.set(); await finish.wait()
        return web.Response(text=json.dumps({'message': {'content': 'OK'}, 'done': False}) + '\n' + json.dumps({'message': {}, 'done': True, 'done_reason': 'stop', 'prompt_eval_count': 5, 'eval_count': 1}) + '\n', content_type='application/x-ndjson')
    provider_app = web.Application(); provider_app.router.add_route('*', '/{path:.*}', provider)
    provider_runner = web.AppRunner(provider_app); await provider_runner.setup()
    provider_site = web.TCPSite(provider_runner, '127.0.0.1', 0); await provider_site.start()
    endpoint = f'http://127.0.0.1:{provider_site._server.sockets[0].getsockname()[1]}'
    config = {'providers': [{'name': 'Local', 'type': 'ollama', 'model': 'wrong-default:8b', 'options': {'endpoint': endpoint, 'options': {'num_predict': 33, 'temperature': .1}}}], 'active_models': {}}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    binding = tmp_path / 'active_models.json'; binding.write_text(json.dumps({'chat': ['Local:wrong-binding:8b'], 'reasoning': ['Local:other:8b']}))
    original_config = (tmp_path / 'config.json').read_bytes(); original_binding = binding.read_bytes()
    sync_entries_from_config()
    @web.middleware
    async def identity(request, handler):
        if request.headers.get('Test-Identity') == 'owner': request['user'] = 'owner'
        if request.headers.get('Test-Identity') == 'app': request['app'] = 'untrusted'
        return await handler(request)
    app = web.Application(middlewares=[identity]); app['local_secret'] = 'test-secret'
    app.router.add_post('/test', api_model_test)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/test'
    monkeypatch.setattr(model_test, '_timeout_secs', lambda: 2)
    owner = {'Test-Identity':'owner'}
    async with ClientSession() as client:
        async def test(ref, use_case='chat', headers=owner):
            async with client.post(url, headers=headers, json={'model': ref, 'use_case': use_case}) as response:
                return response.status, await response.json() if response.status != 403 else {}
        try:
            for headers in ({}, {'Test-Identity':'app'}, {'X-Internal-Secret':'test-secret', 'X-Session-Key':'agent'}):
                assert (await test('Local:ok:8b', headers=headers))[0] == 403
            status, result = await test('Local:ok:8b', 'reasoning')
            assert status == 200 and result['ok'] and result['use_case'] == 'reasoning' and result['model'] == 'Local:ok:8b'
            assert 'OK' in result['detail']
            assert requests[-1]['model'] == 'ok:8b' and requests[-1]['options']['num_predict'] == 33
            assert requests[-1]['options']['temperature'] == .1 and requests[-1]['messages'] == [{'role': 'user', 'content': 'Reply with the single word OK.'}]
            status, refused = await test('Local:refused:8b')
            assert status == 200 and not refused['ok'] and '401' in refused['detail']
            count = len(requests)
            assert (await test('Missing:ok:8b'))[0] == 409
            assert (await test('Local:ok:8b', 'embedding'))[0] == 409
            assert (await test('Local:ok:8b', 'not-a-use-case'))[0] == 400
            assert len(requests) == count
            slow = asyncio.create_task(test('Local:slow:8b'))
            await entered.wait()
            status, busy = await test('Local:ok:8b')
            assert status == 409 and busy['error']['code'] == 'model_test_running'
            finish.set(); assert (await slow)[1]['ok']
            entered.clear(); finish.clear(); monkeypatch.setattr(model_test, '_timeout_secs', lambda: .05)
            status, timeout = await test('Local:slow:8b')
            assert status == 200 and not timeout['ok'] and timeout['reason'] == 'timeout'
            await asyncio.sleep(.05)
            from gideon.security.guardrails.local_inference import _RESOURCES
            from gideon.security.guardrails.budgets import get_meter
            assert not _RESOURCES and not get_meter()._holds
            assert (tmp_path / 'config.json').read_bytes() == original_config and binding.read_bytes() == original_binding
            from gideon.operations.usage_ledger import _path
            usage = [json.loads(line) for line in _path().read_text().splitlines()]
            assert any(row['source'] == 'eval' and row['provider'] == 'Local' and row['model'] == 'ok:8b' and row['input_tokens'] == 5 for row in usage)
        finally:
            finish.set(); await runner.cleanup(); await provider_runner.cleanup()
            registry._entries.clear(); registry._entries.update(original)


def test_provider_declared_refusal_and_mask_failure_are_truthful(monkeypatch):
    assert model_test.untestable_reason('video_gen', 'irrelevant')
    rows = [{'name': 'Unknown', 'models': [{'id':'hosted-image', 'capabilities':['image_gen'], 'downloaded': True}, {'id':'embed', 'capabilities':['embedding']}]}]
    model_test.mark_untestable(rows)
    assert rows[0]['models'][0]['untestable']['image_gen'] and rows[0]['models'][1]['untestable']['embedding']
    monkeypatch.setattr('gideon.security.security.redact_field', lambda _: (_ for _ in ()).throw(ValueError('broken')))
    assert 'private-secret' not in model_test._safe('private-secret')
