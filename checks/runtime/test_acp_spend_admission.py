"""Pre-send ACP admission through the production stdio child and actual native stores."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.core.turn_streams import closing_stream
from gideon.engine.routing.rates import set_rate
from gideon.integrations.llm.acp_agent import AcpAgentProvider
from gideon.integrations.llm.acp_session_provider import AcpSessionProvider
from gideon.integrations.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK
from gideon.operations import usage_ledger
from gideon.security.guardrails import budgets
from gideon.security.guardrails.budgets import Budget, CallCost, Hold, SpendMeter
from gideon.security.guardrails.failure import BudgetExceededError


async def terminal(provider,text):
    async with closing_stream(provider.stream(text)) as events:
        rows=[event async for event in events]
    return next(event for event in rows if event.kind==EVENT_COMPLETE)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True], ids=["direct", "configured-registry"])
async def test_real_acp_paid_admission_usage_linkage_pool_and_unknown_cancel(tmp_path,monkeypatch,configured):
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('GIDEON_HOME',str(home))
    requests=[]
    async def completion(request):
        data=await request.json();text=data['messages'][-1]['content'];requests.append(text)
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await response.prepare(request)
        def frame(delta,finish=None):
            return ('data: '+json.dumps({'id':'actual','object':'chat.completion.chunk','created':1,'model':'paid-model',
                'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})+'\n\n').encode()
        try:
            await response.write(frame({'content':'actual'}))
            await asyncio.sleep(.3 if text=='slow' else .01)
            await response.write(frame({},'stop'))
            await response.write(('data: '+json.dumps({'id':'actual','object':'chat.completion.chunk','created':1,'model':'paid-model','choices':[],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}})+'\n\n').encode())
            await response.write(b'data: [DONE]\n\n')
        except ConnectionResetError:
            pass
        return response
    app=web.Application();app.router.add_post('/v1/chat/completions',completion)
    meter=SpendMeter(config_dir=home)
    monkeypatch.setattr(budgets,'get_meter',lambda:meter)
    monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_dollars=500))
    price_provider='ACPFixture' if configured else 'acp:fixture'
    set_rate(f'{price_provider}:paid-model',{'in_per_mtok':1_000_000,'out_per_mtok':1_000_000})
    async with TestServer(app) as http:
        program='\n'.join([
            'import asyncio',
            'from gideon.integrations.acp.server import AcpStdioServer',
            'from gideon.integrations.llm.openai import OpenAIProvider',
            'from gideon.integrations.llm.credentials import Credential',
            'async def main():',
            f"    provider=OpenAIProvider(model='paid-model',credential=Credential(name='fixture',kind='api_key',secret='fixture'),base_url={str(http.make_url('/v1'))!r})",
            '    await provider.start()',
            '    server=AcpStdioServer()',
            "    server.sessions['actual']=provider",
            '    await server.serve()',
            'asyncio.run(main())',
        ])
        env={'GIDEON_HOME':str(home),'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'runtime')}
        if configured:
            from gideon.extensions.providers.provider_bridge import create_provider_factory
            from gideon.integrations.llm import acp_agent
            from gideon.integrations.llm.registry import ProviderEntry, get_default_registry
            acp_agent._register_provider()
            get_default_registry().register_entry(ProviderEntry(name='ACPFixture',type='acp_agent',model='paid-model',
                options={'command':[sys.executable,'-c',program],'env':env,'sandbox_mode':'none'}))
            provider=create_provider_factory('loops')(session_key='fixture',model_override='ACPFixture:paid-model',cwd=str(tmp_path))
            assert isinstance(provider,AcpAgentProvider) and provider.spend_axis=='loops'
            assert provider.served_model_ref=='ACPFixture:paid-model'
        else:
            provider=AcpAgentProvider(command=[sys.executable,'-c',program],cwd=tmp_path,
                env=env,model='paid-model',runtime_id='acp:fixture',sandbox_mode='none')
        client=provider._client
        try:
            await client._open_connection();await client._connection.initialize({'protocolVersion':1})
            client._session=client._connection._bind_session('actual');client._session_id='actual'
            if not configured:
                provider.set_spend_axis('loops')
            first=await terminal(provider,'first')
            assert first.input_tokens==100 and first.output_tokens==20
            assert first.tool_meta['charged_cost_usd']==120 and first.tool_meta['spend_charged']
            assert meter.day_totals().dollars==120 and meter.held()==(0,0)
            set_rate(f'{price_provider}:paid-model',{'in_per_mtok':2_000_000,'out_per_mtok':2_000_000})
            usage_ledger.record_from_event(first,source='loop',session_key='fixture',provider=price_provider,model='paid-model')
            ledger=usage_ledger.UsageJournal(usage_ledger._path()).rows()
            assert ledger[-1]['cost_usd']==120 and ledger[-1]['audit_id']==first.tool_meta['audit_id']
            set_rate(f'{price_provider}:paid-model',{'in_per_mtok':1_000_000,'out_per_mtok':1_000_000})
            other=meter.admit(CallCost('other:fixed',known_dollars=300,unit_only=True),day=Budget(max_dollars=500))
            assert isinstance(other,Hold)
            queued=asyncio.create_task(terminal(provider,'queued'))
            await asyncio.sleep(.15);assert requests==['first'] and not queued.done()
            meter.release(other)
            second=await asyncio.wait_for(queued,5)
            assert second.tool_meta['charged_cost_usd']==120 and meter.day_totals().dollars==240
            if configured:
                audits=[json.loads(line) for line in (home/'model_calls.jsonl').read_text().splitlines()]
                assert len(audits)==2 and len({row['audit_id'] for row in audits})==2
                assert meter.held()==(0,0)
                return
            pooled=AcpSessionProvider(client._connection,client._session,runtime_id='acp:fixture',model='paid-model')
            pooled.set_spend_axis('loops')
            third=await terminal(pooled,'pooled')
            assert third.tool_meta['charged_cost_usd']==120 and meter.day_totals().dollars==360
            with pytest.raises(BudgetExceededError):
                pooled._model='unknown-model'
                await terminal(pooled,'unknown-must-not-send')
            assert requests==['first','queued','pooled']
            pooled._model='paid-model'
            partial=provider.stream('slow')
            assert (await anext(partial)).kind==EVENT_TEXT_CHUNK
            await partial.aclose()
            assert meter.held()==(0,0) and meter.day_totals().unpriced==1
            assert meter.day_totals().dollars==360
            # The owner's attended CLI remains uncapped even after known paid spend reaches cap.
            provider.set_spend_axis('')
            monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_dollars=1))
            attended=await terminal(provider,'attended')
            assert not attended.tool_meta.get('spend_charged') and meter.day_totals().dollars==360
            audits=[json.loads(line) for line in (home/'model_calls.jsonl').read_text().splitlines()]
            assert len(audits)==5
            assert len({row['audit_id'] for row in audits})==5
            assert audits[-1]['priced'] is False
        finally:
            await client.shutdown()


def test_real_native_factory_marks_only_metered_axes():
    from gideon.extensions.providers.provider_bridge import create_provider_factory
    assert create_provider_factory('loops').spend_axis=='loops'
    assert create_provider_factory('chat').spend_axis==''
