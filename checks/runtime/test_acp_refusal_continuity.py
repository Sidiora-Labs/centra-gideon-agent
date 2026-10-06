"""Refusal continuity using the supported scripted backend and real ACP stdio."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.dialect import ZedAdapterDialect
from gideon.integrations.acp.errors import AcpError
from gideon.core.turn_streams import closing_stream
from gideon.security.approval_brief import compose_approval_brief, entry_approval_brief


@pytest.mark.parametrize('kind,stop,stop_user,expected_prompts', [
    ('rejectStop','cancelled',False,5),
    ('rejectContinue','end_turn',False,1),
    ('rejectStop','cancelled',True,1),
])
@pytest.mark.asyncio
async def test_deny_carry_on_is_bounded_and_stop_is_terminal(tmp_path,monkeypatch,kind,stop,stop_user,expected_prompts):
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('GIDEON_HOME',str(home))
    script=tmp_path/'playback.json'
    script.write_text(json.dumps({'version':1,'on_exhausted':'repeat_last','turns':[{'stop_reason':stop,'tool_calls':[{'id':'call','name':'write /tmp/local-picture.png','input':{},'requires_approval':True,'options':[{'optionId':kind,'kind':'reject_once','name':'Deny and stop' if kind=='rejectStop' else 'Deny and continue'}]}]}]}))
    record=tmp_path/'frames.jsonl'
    program='\n'.join([
        'import asyncio,json',
        'from gideon.integrations.acp.server import AcpStdioServer',
        'from gideon.integrations.llm.scripted import ScriptedProvider',
        'async def main():',
        '    provider=ScriptedProvider()',
        '    await provider.start()',
        '    server=AcpStdioServer()',
        "    server.sessions['local']=provider",
        '    dispatch=server.dispatch',
        '    async def recorded(frame):',
        f"        with open({str(record)!r},'a') as output: output.write(json.dumps(frame)+'\\n')",
        '        await dispatch(frame)',
        '    server.dispatch=recorded',
        '    await server.serve()',
        'asyncio.run(main())',
    ])
    client=AcpClient(dialect=ZedAdapterDialect(),work_dir=tmp_path,command=[sys.executable,'-c',program],sandbox_mode='none',extra_env={'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'runtime'),'GIDEON_HOME':str(home),'GIDEON_SCRIPTED_MODEL_SCRIPT':str(script),'GIDEON_CREDENTIAL_BACKEND':'dotenv'})
    events=[];consequences=[]
    try:
        await client._open_connection()
        await client._connection.initialize({'protocolVersion':1})
        client._session=client._connection._bind_session('local');client._session_id='local'
        async with closing_stream(client.stream_events('Original task',timeout=15)) as stream:
            async for event in stream:
                events.append(event)
                if event.kind=='permission_request':
                    consequences.append(event.tool_meta['deny_consequence'])
                    brief=compose_approval_brief(event)
                    assert brief['denyConsequence']==consequences[-1]
                    assert 'Deny' in brief['summary']
                    if stop_user:
                        await client._session.cancel()
                    else:
                        await client.reject_tool(event.request_id)
                if event.kind=='complete': break
        assert sum(e.kind=='complete' for e in events)==1
        assert sum(e.kind=='carried_on' for e in events)==expected_prompts-1
        assert not client._session._turn_lock.locked()
        if kind=='rejectContinue': assert consequences==['declines']
        elif not stop_user: assert consequences==['carries_on']*4+['ends']
        frames=[json.loads(line) for line in record.read_text().splitlines()]
        prompts=[f['params']['prompt'] for f in frames if f.get('method')=='session/prompt']
        assert len(prompts)==expected_prompts
        assert all(len(p)==1 and p[0]['type']=='text' for p in prompts)
        assert all('without these steps' in p[0]['text'] for p in prompts[1:])
        with pytest.raises(AcpError, match='could not open a session'):
            await client._connection.new_session({'cwd':str(tmp_path/'missing'), 'mcpServers':[]})
    finally:
        await client.shutdown()


def test_registry_approval_retains_consequence():
    brief=entry_approval_brief({'tool':'write','deny_consequence':'ends'})
    assert brief['denyConsequence']=='ends'
    assert 'continuation limit' in brief['summary']
