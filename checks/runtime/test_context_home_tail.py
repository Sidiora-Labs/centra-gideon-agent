"""Actual context assembly carries account-home meaning beyond its cap to the SDK wire."""
import json
import os
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from gideon.cognition.context import PromptAssembler, _MAX_CONTEXT_CHARS
from gideon.cognition.memory import MemoryJournal
from gideon.extensions.skills import ProcedureLibrary
from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.events import EVENT_COMPLETE
from gideon.core.turn_streams import closing_stream

@pytest.mark.asyncio
@pytest.mark.parametrize('agent,mode,blocks_reads',[('gideon','',False),('custom','',False),('custom','private',True)])
async def test_capped_actual_context_home_tail_reaches_sdk_without_widening_roots(tmp_path,agent,mode,blocks_reads):
    workspace=tmp_path/'workspace';workspace.mkdir()
    builder=PromptAssembler(memory=MemoryJournal(workspace=workspace),
        skills=ProcedureLibrary(skills_path=tmp_path/'skills',install_builtins=False))
    notices=[]
    context=builder.build_session_context(session_key='dashboard:home-tail',agent=agent,cwd=str(workspace),
        compressed_history='history line\n'*(_MAX_CONTEXT_CHARS//8),mode=mode,blocks_reads=blocks_reads,dropped_out=notices)
    actual_home=os.path.expanduser('~');notes=Path(actual_home)/'Notes'
    tail=f'[HOME DIRECTORY] {actual_home}: ~ in a path is this folder, so ~/Notes is {notes}\n'
    assert notices and tail in context
    assert context.index(tail)>=_MAX_CONTEXT_CHARS-20
    assert context.index('[CURRENT DATE]')>context.index(tail)
    tools=NativeBuiltinToolProvider(cwd=workspace,extra_roots=[notes],sandbox_mode='none')
    assert tools._resolve('~/Notes/Garden')==notes/'Garden'
    restricted=NativeBuiltinToolProvider(cwd=workspace,sandbox_mode='none')
    with pytest.raises(ValueError,match='escapes the workspace root'):
        restricted._resolve('~/Notes/Garden')
    captured=[]
    async def completion(request):
        captured.append(await request.json())
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await response.prepare(request)
        frame={'id':'actual','object':'chat.completion.chunk','created':1,'model':'fixture',
            'choices':[{'index':0,'delta':{'content':'observed'},'finish_reason':'stop'}]}
        await response.write(('data: '+json.dumps(frame)+'\n\ndata: [DONE]\n\n').encode())
        return response
    app=web.Application();app.router.add_post('/v1/chat/completions',completion)
    async with TestServer(app) as http:
        provider=OpenAIProvider(model='fixture',credential=Credential(name='fixture',kind='api_key',secret='fixture'),base_url=str(http.make_url('/v1')))
        await provider.start()
        try:
            async with closing_stream(provider.complete([{'role':'system','content':context},{'role':'user','content':'Review ~/Notes/Garden'}])) as events:
                result=[event async for event in events]
            assert result[-1].kind==EVENT_COMPLETE
            assert captured[0]['messages'][0]['content']==context
            assert tail in captured[0]['messages'][0]['content']
        finally:
            await provider.shutdown()
