"""Catalog metadata is preserved as explicit job authority."""
import pytest
import gideon.sdk.model
from gideon.integrations.llm.catalog import ModelInfo, infer_capabilities, _decode_model_rows
from gideon.integrations.local_models.provider import LocalModel
from gideon.integrations.local_models.registry import to_local_model

@pytest.mark.parametrize('model',['bge-reranker-v2','llama-guard:8b','gpt-4o-realtime','gpt-5.2-pro','gpt-3.5-turbo-instruct','o3-deep-research'])
def test_unbound_families_are_not_offered(model):
    assert infer_capabilities(model)==[]
    assert ModelInfo(model,model).to_dict()['capabilities']==[]

def test_alias_and_vendor_record_are_authoritative():
    assert _decode_model_rows([{'id':'alias','root':'bge-reranker'}])[0].capabilities==[]
    records=[{'id':'first','kind':'embedding'},{'id':'second','kind':'chat'}]
    assert [r.capabilities for r in _decode_model_rows(records,lambda r:[r['kind']])]==[['embedding'],['chat']]
    assert _decode_model_rows(records,lambda r:1/0)[0].capabilities==[]

def test_multiple_provider_jobs_do_not_fill_an_explicit_empty_model():
    model=LocalModel('reranker')
    assert to_local_model(model,capabilities=['chat','embedding']).capabilities==[]
    assert to_local_model(model,capabilities=['stt']).capabilities==['stt']
    assert model.capabilities==[]

@pytest.mark.asyncio
async def test_native_binding_rejects_known_embedding_and_keeps_unknown(tmp_path,monkeypatch):
    import importlib.util,json,sys
    from pathlib import Path
    from aiohttp import web,ClientSession
    from gideon.core.config import loader
    from gideon.integrations.llm.registry import get_default_registry,set_default_registry,ProviderRegistry
    from gideon.interfaces.dashboard.handlers.model_registry import register_model_registry_routes
    from gideon.stale_write import revision_of
    monkeypatch.setattr(loader,'config_dir',lambda:tmp_path)
    monkeypatch.setattr(loader,'resolve_config_dir',lambda:tmp_path)
    old=get_default_registry();registry=ProviderRegistry();set_default_registry(registry)
    server=web.Application()
    server.router.add_get('/api/tags',lambda request:web.json_response({'models':[{'name':'embed:latest','capabilities':['embedding']}]}))
    runner=web.AppRunner(server);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    endpoint=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'name':'Ollama','type':'ollama','options':{'endpoint':endpoint}}]}))
    path=Path(__file__).resolve().parents[2]/'runtime/gideon/extensions/apps/native/ollama-models/provider.py'
    spec=importlib.util.spec_from_file_location('gideon_ollama_binding_integration',path);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    target=web.Application();register_model_registry_routes(target);target_runner=web.AppRunner(target);await target_runner.setup();target_site=web.TCPSite(target_runner,'127.0.0.1',0);await target_site.start()
    base=f'http://127.0.0.1:{target_site._server.sockets[0].getsockname()[1]}'
    try:
        async with ClientSession() as client:
            headers={'If-Match':f'"{revision_of([])}"'}
            async with client.put(base+'/api/models/active/chat',json={'models':['Ollama:embed:latest']},headers=headers) as response:
                assert response.status==400
                assert (await response.json())['error']['code']=='model_cannot_serve_use_case'
            async with client.put(base+'/api/models/active/chat',json={'models':['Ollama:unknown']},headers=headers) as response:
                assert response.status==200
                assert (await response.json())['models']==['Ollama:unknown']
    finally:
        await target_runner.cleanup();await runner.cleanup();set_default_registry(old);sys.modules.pop(spec.name,None)
