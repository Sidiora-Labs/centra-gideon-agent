"""Real SDK/native failed turns retain charged accounting through stored Usage APIs."""
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from gideon.core.turn_streams import closing_stream
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.engine.routing.rates import set_rate
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.events import EVENT_SPENT
from gideon.integrations.llm_helpers import stream_and_collect
from gideon.integrations.tool_providers.base import ToolProvider, ToolDefinition, ToolResult
from gideon.security.guardrails.model_call import ModelCallGuard
from gideon.security.guardrails.budgets import SpendMeter, Budget
from gideon.operations import usage_ledger
from gideon.interfaces.dashboard.handlers.usage import api_usage_totals

class EchoTool(ToolProvider):
    name='echo'; display_name='Echo'
    async def list_tools(self):
        return [ToolDefinition(name='echo',description='Echo',parameters={'type':'object','properties':{}},requires_approval=False)]
    async def invoke(self,name,args):
        return ToolResult(success=True,output='actual tool result')

@pytest.mark.asyncio
async def test_actual_sdk_failed_native_turn_immutable_store_and_api(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('GIDEON_HOME',str(home))
    calls=[]
    async def response(request):
        body=await request.json();calls.append(body)
        result=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await result.prepare(request)
        def frame(delta,finish=None,usage=None):
            return ('data: '+json.dumps({'id':'actual','object':'chat.completion.chunk','created':1,'model':'paid',
                'choices':[] if usage else [{'index':0,'delta':delta,'finish_reason':finish}], **({'usage':usage} if usage else {})})+'\n\n').encode()
        if len(calls)==1:
            await result.write(frame({'tool_calls':[{'index':0,'id':'call1','type':'function','function':{'name':'echo','arguments':'{}'}}]}))
            await result.write(frame({},'tool_calls'))
            await result.write(frame({},usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}))
            await result.write(b'data: [DONE]\n\n')
        else:
            await result.write(frame({'content':'partial'}))
            await result.write(frame({},usage={'prompt_tokens':30,'completion_tokens':4,'total_tokens':34}))
            # Actual wire EOF with usage, but no required finish signal.
        return result
    app=web.Application();app.router.add_post('/v1/chat/completions',response)
    set_rate('fixture:paid',{'in_per_mtok':1_000_000,'out_per_mtok':1_000_000})
    meter=SpendMeter(config_dir=home)
    async with TestServer(app) as http:
        model=OpenAIProvider(model='paid',credential=Credential(name='fixture',kind='api_key',secret='fixture'),base_url=str(http.make_url('/v1')))
        guard=ModelCallGuard(model,use_case='loops',provider_name='fixture',model='paid',meter=meter,budget=Budget(max_dollars=1000))
        guard.served_model_ref='fixture:paid'
        runtime=NativeAgentRuntime(definition=AgentRuntimeDefinition(name='Fixture',provider='native',model='paid'),model_provider=guard,tool_providers=[EchoTool()])
        await runtime.start();seen=[]
        try:
            with pytest.raises(Exception):
                await stream_and_collect(runtime,'exercise tool then fail',on_complete=seen.append)
            assert len(calls)==2 and len(seen)==1 and seen[0].kind==EVENT_SPENT
            spent=seen[0]
            assert spent.input_tokens==100 and spent.output_tokens==20
            assert spent.tool_meta['charged_cost_usd']==120
            assert len(spent.tool_meta['audit_ids'])==2
            assert meter.day_totals().dollars==120 and meter.day_totals().unpriced==1 and meter.held()==(0,0)
            set_rate('fixture:paid',{'in_per_mtok':9_000_000,'out_per_mtok':9_000_000})
            usage_ledger.record_from_event(spent,source='chat',session_key='fixture',provider='fixture',model='paid')
            stored=usage_ledger.UsageJournal(usage_ledger._path()).rows()
            assert stored[-1]['cost_usd']==120 and stored[-1]['priced'] is False
            assert len(stored[-1]['audit_ids'])==2
            api=web.Application();api.router.add_get('/usage',api_usage_totals)
            async with TestClient(TestServer(api)) as client:
                payload=await (await client.get('/usage')).json()
                assert payload['totals']['cost_usd']==120
            from gideon.engine.routing import usage
            fold=usage.rebuild(home)
            assert fold['uncounted']['calls']==0
        finally:
            await runtime.shutdown()
