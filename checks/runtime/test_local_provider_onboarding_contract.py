"""Additional local provider setup preserves the existing choice via real local HTTP."""
import asyncio
import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from aiohttp import web, ClientSession
from gideon.operations import local_model_detect as detect, seed_local_model as seed
from gideon.interfaces.dashboard.handlers.local_model import register_local_model_routes
from gideon.interfaces.dashboard.handlers.providers import api_providers_list
from gideon.integrations.llm.registry import get_default_registry, sync_entries_from_config
from test_seed_local_model import _install_stub_provider_app


@pytest.mark.asyncio
async def test_real_detection_add_reload_collision_owner_and_no_rebind(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    registry = get_default_registry(); original = dict(registry._entries); registry._entries.clear()
    source = Path(__file__).parents[2] / 'runtime/gideon/extensions/apps/native/ollama-models/provider.py'
    spec = importlib.util.spec_from_file_location('onboarding_contract_ollama', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    _install_stub_provider_app(tmp_path)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'models': [
                {'name':'new-vision:8b','modified_at':'2026-10-07','capabilities':['completion','vision']},
                {'name':'tools-chat:8b','modified_at':'2026-10-01','capabilities':['completion','tools']},
                {'name':'nomic-embed:latest','modified_at':'2026-10-07','capabilities':['embedding']}
            ]}).encode())
        def log_message(self,*args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    (tmp_path / 'config.json').write_text(json.dumps({'providers':[{'name':'Original','type':'unregistered-existing','model':'chosen:8b'}]}))
    binding = tmp_path / 'active_models.json'; binding.write_text(json.dumps({'chat':['Original:chosen:8b'],'reasoning':['Original:reason:8b'],'embedding':['Original:embed']}))
    original_binding = binding.read_bytes(); sync_entries_from_config()
    real_detect = detect.detect_localhost
    monkeypatch.setattr(detect, 'detect_localhost', lambda: real_detect(endpoint=endpoint))
    @web.middleware
    async def identity(request, handler):
        if request.headers.get('Test-Identity') == 'owner': request['user'] = 'owner'
        if request.headers.get('Test-Identity') == 'app': request['app'] = 'untrusted'
        return await handler(request)
    app = web.Application(middlewares=[identity]); app['local_secret'] = 'test-secret'; register_local_model_routes(app)
    app.router.add_get('/providers', api_providers_list)
    runner = web.AppRunner(app); await runner.setup(); site = web.TCPSite(runner,'127.0.0.1',0); await site.start()
    base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    owner = {'Test-Identity':'owner'}
    async with ClientSession() as client:
        try:
            for headers in ({},{'Test-Identity':'app'},{'X-Internal-Secret':'test-secret'}):
                async with client.get(base+'/api/onboarding/local-model',headers=headers) as response: assert response.status == 403
            assert not calls
            async with client.get(base+'/api/onboarding/local-model',headers=owner) as response:
                found = await response.json(); assert found['detected'] and found['model'] == 'tools-chat:8b' and not found['provider']
            for body in ({'endpoint':endpoint}, {'endpoint':'http://8.8.8.8:11434','bind_chat':False}, {'endpoint':endpoint,'bind_chat':'false'}):
                async with client.post(base+'/api/onboarding/local-model/bind',headers=owner,json=body) as response: assert response.status == 400
            async with client.post(base+'/api/onboarding/local-model/bind',headers=owner,json={'endpoint':endpoint,'bind_chat':False}) as response:
                added = await response.json(); assert response.status == 200 and added['status'] == seed.ADDED and added['provider'] == 'Local Ollama' and added['model'] == 'tools-chat:8b'
            assert binding.read_bytes() == original_binding
            config = (tmp_path / 'config.json').read_bytes(); assert b'api_key' not in config
            async with client.get(base+'/api/onboarding/local-model',headers=owner) as response:
                reloaded = await response.json(); assert reloaded['provider'] == added['provider']
            async with client.get(base+'/providers',headers=owner) as response:
                names = [row['name'] for row in (await response.json())['providers']]
                assert names == ['Original','Local Ollama']
            async with client.post(base+'/api/onboarding/local-model/bind',headers=owner,json={'endpoint':endpoint,'bind_chat':False}) as response:
                assert (await response.json())['status'] == seed.ALREADY_BOUND
            async with client.post(base+'/api/onboarding/local-model/bind',headers=owner,json={'endpoint':f'http://127.0.0.1:{server.server_port+1}','bind_chat':False}) as response:
                assert response.status == 409 and (await response.json())['error']['code'] == seed.SKIPPED_NAME_TAKEN
            assert (tmp_path / 'config.json').read_bytes() == config and binding.read_bytes() == original_binding
        finally:
            await runner.cleanup(); server.shutdown(); server.server_close(); thread.join()
            registry._entries.clear(); registry._entries.update(original)


def test_scan_is_bounded_private_opt_in_and_seed_pick_capability_order():
    contacted = []
    def fake(endpoint):
        contacted.append(endpoint); return detect.DetectedEndpoint(endpoint,'chat')
    found = detect.scan_local_network(candidates=['8.8.8.8','127.0.0.1','169.254.169.254','192.0.2.1','192.168.7.8'],prober=fake)
    assert contacted == ['http://192.168.7.8:11434'] and len(found) == 1
    assert not detect.endpoint_is_local('http://localhost:123@8.8.8.8')
    assert not detect.endpoint_is_local('http://169.254.169.254')
    assert not detect.endpoint_is_local('http://192.168.7.8:11434/?token=secret')
    assert detect.endpoint_identity('http://localhost:11434/') == detect.endpoint_identity('http://127.0.0.1:11434')
    from gideon.integrations.llm.catalog import ModelInfo
    assert seed.pick_model([ModelInfo(id='vision',name='vision',capabilities=['chat','image_modality'],extra={'modified_at':'2026-10-07'}),ModelInfo(id='tools',name='tools',capabilities=['chat','tools'],extra={'modified_at':'2026-10-01'})],'chat') == 'tools'
