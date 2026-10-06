"""Slack approval answers remain bound to a live native offer and its workspace."""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runtime/gideon/extensions/apps/native/gideonai-slack-desk'))
from slack_desk_runtime import enterprise,handler,interactions
from slack_desk_runtime.client import RealSlackDeskClient
from slack_desk_runtime.delivery import SlackDeskDelivery
from gideon.security import session_credentials
from gideon.security.approval_answer import on_channel,YOU
from gideon.integrations import channel_delivery,channel_transports,channel_trust

@pytest.fixture
async def native(tmp_path,monkeypatch):
    from gideon.core.config import AppConfig,loader,credentials
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard import session_store
    from gideon.interfaces.dashboard.state import ConsoleState,_ChatSession
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path));monkeypatch.delenv('GIDEON_DISABLE_LIVE_WRITES',raising=False)
    for module in (loader,session_store):monkeypatch.setattr(module,'config_dir',lambda:tmp_path)
    monkeypatch.setattr(channel_transports,'_transports',dict(channel_transports._transports))
    monkeypatch.setattr(channel_delivery,'_REGISTRY',dict(channel_delivery._REGISTRY));monkeypatch.setattr(channel_delivery,'_QUEUES',dict(channel_delivery._QUEUES))
    monkeypatch.setattr(handler,'_pending_approvals',{});monkeypatch.setattr(handler,'_owner_id','UOWNER');monkeypatch.setattr(handler,'_allowed_users',{'UOWNER','UOTHER'})
    (tmp_path/'config.json').write_text('{}')
    credentials.save_credential(credentials.owner_id_credential('slack'),'UOWNER');channel_trust.allow_sender('slack','UOWNER')
    calls=[];private={'value':True,'fail':False}
    async def endpoint(request):
        payload=await request.json() if request.content_type == "application/json" else dict(await request.post())
        payload.update(dict(request.query))
        for k in ('blocks',):
            if k in payload and isinstance(payload[k],str):payload[k]=json.loads(payload[k])
        calls.append((request.match_info['method'],payload))
        method=request.match_info['method']
        if method=='auth.test':result={'ok':True,'team_id':'TLOCAL','user_id':'UBOT'}
        elif method=='conversations.info':result={'ok':not private['fail'],'channel':{'id':payload.get('channel'),'is_im':private['value']}}
        else:result={'ok':True,'ts':'123.456','channel':payload.get('channel')}
        return web.json_response(result)
    app=web.Application();app.router.add_route('*','/api/{method}',endpoint)
    async with TestServer(app) as server:
        client=RealSlackDeskClient('xoxb-local');client._web.base_url=str(server.make_url('/api/'))
        auth=await client.auth_test();monkeypatch.setattr(enterprise,'_validated_team_id',auth['team_id'])
        directory=ConversationDirectory(AppConfig.load());state=ConsoleState(directory,time.time());session=_ChatSession('ordinary');state._sessions[session.key]=session
        state.link_channel('ordinary','111.222','DLOCAL','slack')
        runtime=SimpleNamespace(_owner_id='UOWNER',slack_desk=client)
        transport=SimpleNamespace(name='slack',connected=True);channel_transports.register_transport(transport)
        delivery=SlackDeskDelivery(client,'UOWNER',runtime=runtime,transport=transport);channel_delivery.register(delivery,'slack')
        monkeypatch.setattr(interactions,'_orch',runtime)
        credential=session_credentials.begin_turn('dashboard:ordinary',on_channel('slack','UOWNER','slack:TLOCAL'),turn_id='one',memory_mode='persistent')
        n=SimpleNamespace(state=state,session=session,client=client,delivery=delivery,transport=transport,runtime=runtime,calls=calls,private=private)
        try:yield n
        finally:
            session_credentials.end_turn(credential)
            for task in tuple(state._background_tasks):task.cancel()
            await asyncio.gather(*tuple(state._background_tasks),return_exceptions=True)
            if client._web.session is not None:await client._web.session.close()

def register(n,risk='caution'):
    future=asyncio.get_running_loop().create_future();n.session._approval_futures['call']=future
    n.session.append('permission','Read file',json.dumps({'request_id':'call','asked_by':'agent:ordinary'}))
    n.state._register_chat_approval({'id':'call','session':'ordinary','tool':'read_file','tool_input':'x'*7000,'risk':risk,'blast_radius':{'readOnly':True}})
    return future

async def pending(n):
    for _ in range(300):
        if handler._pending_approvals:return next(iter(handler._pending_approvals.values()))
        await asyncio.sleep(.005)
    raise AssertionError('no native Slack prompt')

def payload(p,answer='trust'):
    return {'type':'block_actions','team':{'id':'TLOCAL'},'user':{'id':'UOWNER'},'channel':{'id':p.channel},'message':{'ts':'123.456','thread_ts':p.thread},'actions':[{'action_id':{'trust':'trust_tool','approved':'approve_tool','rejected':'reject_tool'}[answer],'value':str(p.request_id)}]}

@pytest.mark.asyncio
@pytest.mark.parametrize('answer',['approved','trust','rejected'])
async def test_actual_local_api_and_native_callback_apply_exact_offer(native,answer):
    f=register(native);p=await pending(native)
    blocks=next(body['blocks'] for method,body in native.calls if method=='chat.postMessage')
    buttons=next(b['elements'] for b in blocks if b['type']=='actions')
    assert [b['text']['text'] for b in buttons]==['Allow once','Allow for this chat','Deny']
    sections=[b['text']['text'] for b in blocks if b['type']=='section']
    assert sum(text.count('x') for text in sections)==7000
    await interactions.dispatch(payload(p,answer))
    assert await f==('rejected' if answer=='rejected' else 'approved')
    assert native.session._trust is (answer=='trust')
    await asyncio.gather(*tuple(native.state._background_tasks),return_exceptions=True)
    updates=[body for method,body in native.calls if method=='chat.update']
    assert updates and not any(block['type']=='actions' for block in updates[-1]['blocks'])
    assert ('Every tool in this chat' in updates[-1]['text']) is (answer=='trust')
    native.session._trust=False
    await interactions.dispatch(payload(p,'trust'))
    assert not native.session._trust

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['user','team','channel','message','thread','value','unknown','owner','unpaired','transport','link','ceiling'])
async def test_changed_live_identity_destination_or_capability_cannot_grant(native,monkeypatch,change):
    from gideon.core.config import credentials
    f=register(native);p=await pending(native);body=payload(p)
    if change=='user':body['user']['id']='UOTHER'
    elif change=='team':body['team']['id']='TFOREIGN'
    elif change=='channel':body['channel']['id']='DOTHER'
    elif change=='message':body['message']['ts']='999.999'
    elif change=='thread':body['message']['thread_ts']='999.999'
    elif change=='value':body['actions'][0]['value']='wrong'
    elif change=='unknown':body['actions'][0]['action_id']='other_tool'
    elif change=='owner':credentials.save_credential(credentials.owner_id_credential('slack'),'UOTHER')
    elif change=='unpaired':channel_trust.deny_sender('slack','UOWNER')
    elif change=='transport':channel_transports.register_transport(SimpleNamespace(name='slack',connected=True))
    elif change=='link':native.state.link_channel('ordinary','333.444','DLOCAL','slack')
    elif change=='ceiling':monkeypatch.setattr('gideon.security.approval_grants.stands',lambda *a,**k:False)
    await interactions.dispatch(body)
    assert not f.done() and not native.session._trust and not p.future.done()
    native.state.cancel_approval('ordinary:call',reason='finished')

@pytest.mark.asyncio
@pytest.mark.parametrize('scope',['group','api_failure','unchecked','app'])
async def test_unavailable_private_or_native_scope_has_no_standing_offer(native,scope):
    if scope=='group':native.private['value']=False
    if scope=='api_failure':native.private['fail']=True
    if scope=='app':native.session._created_by_app='example'
    f=register(native,risk='unchecked' if scope=='unchecked' else 'caution');p=await pending(native)
    assert [a.key for a in p.answers]==['approved','rejected']
    await interactions.dispatch(payload(p,'trust'));assert not f.done()
    native.state.cancel_approval('ordinary:call',reason='finished')

@pytest.mark.asyncio
@pytest.mark.parametrize('ending',['approved','expired','cancelled'])
async def test_native_owner_endings_close_offer_without_late_grant(native,ending):
    f=register(native);p=await pending(native)
    if ending=='approved':native.state.resolve_approval('ordinary:call',True,by=YOU)
    else:native.state.end_approval('ordinary:call',outcome=ending)
    await asyncio.gather(*tuple(native.state._background_tasks),return_exceptions=True)
    assert p.future.done()
    await interactions.dispatch(payload(p,'trust'));assert not native.session._trust
    assert any(ending.capitalize() in body['text'] for method,body in native.calls if method=='chat.update')
