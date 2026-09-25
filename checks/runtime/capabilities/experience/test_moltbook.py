import asyncio,json
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import pytest
from gideon.integrations.llm.credentials import CredentialStore
from gideon.interfaces.dashboard.handlers.capabilities_experience_moltbook import PREFIX,register
from gideon.workspace.capabilities.experience.moltbook import DEFAULT_BASE,MoltbookAdapter,MoltbookError,safe_base
from gideon.workspace.capabilities.experience.moltbook_tools import MoltbookTools

API_KEY='moltbook_test_secret_123'
def credential(home,name='moltbook-main'):
    store=CredentialStore(home);store.put(name,{'type':'api_key','value':API_KEY});return name
def protocol():
    calls=[]
    @web.middleware
    async def auth(request,handler):
        calls.append({'method':request.method,'path':request.path_qs,'authorization':request.headers.get('Authorization'),'body':await request.json() if request.can_read_body else None})
        if request.headers.get('Authorization')!='Bearer '+API_KEY:return web.json_response({'error':'unauthorized'},status=401)
        return await handler(request)
    app=web.Application(middlewares=[auth])
    app.router.add_get('/api/v1/agents/me',lambda request:web.json_response({'id':'agent-1','name':'GideonMolty','claimed':True}))
    app.router.add_get('/api/v1/agents/status',lambda request:web.json_response({'status':'claimed','karma':9}))
    async def feed(request):return web.json_response({'posts':[{'id':'post-1','title':'Protocol news','content':'Current feed bytes','author':{'name':'PeerMolty'}}],'has_more':False})
    app.router.add_get('/api/v1/feed',feed)
    app.router.add_get('/api/v1/posts/{post}/comments',lambda request:web.json_response({'comments':[{'id':'comment-1','content':'Existing reply'}]}))
    async def post(request):
        body=calls[-1]['body']
        if body['title']=='Rate limited':return web.json_response({'error':'post cooldown'},status=429,headers={'Retry-After':'30'})
        return web.json_response({'post':{'id':'remote-post'},'verification_required':True,'verification':{'code':'verify-1','challenge':'not persisted','expires_at':'2030-01-01T00:00:00Z'}},status=201)
    async def comment(request):return web.json_response({'id':'remote-comment','post_id':request.match_info['post']},status=201)
    app.router.add_post('/api/v1/posts',post);app.router.add_post('/api/v1/posts/{post}/comments',comment)
    return app,calls

async def service(tmp_path):
    app,calls=protocol();server=TestServer(app);await server.start_server();adapter=MoltbookAdapter(tmp_path,str(server.make_url('/api/v1')).rstrip('/'));credential(tmp_path);adapter.configure({'credential_ref':'moltbook-main'});return adapter,server,calls

def post_payload(**changes):return {'request_id':'post-request','kind':'post','submolt':'general','title':'A reviewed update','content':'Exact approved content.',**changes}
def comment_payload(**changes):return {'request_id':'comment-request','kind':'comment','post_id':'post-1','content':'A deliberate reply.',**changes}

def test_configuration_uses_named_credential_and_never_exposes_secret(tmp_path):
    adapter=MoltbookAdapter(tmp_path)
    assert adapter.config()=={'configured':False,'base_url':DEFAULT_BASE,'registration_supported':False,'external_qualified':False}
    with pytest.raises(MoltbookError) as absent:adapter.configure({'credential_ref':'missing'})
    assert absent.value.code=='credential_missing'
    reference=credential(tmp_path)
    configured=adapter.configure({'credential_ref':reference})
    assert configured['configured'] and configured['credential_ref']==reference
    assert configured['base_url']==DEFAULT_BASE and not configured['registration_supported'] and not configured['external_qualified']
    assert API_KEY not in json.dumps(configured)
    assert API_KEY not in adapter.path.read_bytes().decode(errors='ignore')
    assert API_KEY not in json.dumps(adapter.config())


def test_endpoint_policy_requires_official_www_or_explicit_loopback():
    assert safe_base(DEFAULT_BASE+'/')==DEFAULT_BASE
    assert safe_base('http://127.0.0.1:8765/api/v1')=='http://127.0.0.1:8765/api/v1'
    for endpoint in ('https://moltbook.com/api/v1','https://evil.example/api/v1','https://key@www.moltbook.com/api/v1','https://www.moltbook.com/api/v1?key=x'):
        with pytest.raises(MoltbookError):safe_base(endpoint)


def test_real_protocol_reads_profile_status_feed_and_comments(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        try:
            assert (await adapter.read('profile'))['name']=='GideonMolty'
            assert (await adapter.read('status'))['status']=='claimed'
            feed=await adapter.read('feed',sort='new',limit=15);assert feed['posts'][0]['id']=='post-1'
            comments=await adapter.read('comments',post_id='post-1');assert comments['comments'][0]['id']=='comment-1'
            assert [row['path'] for row in calls]==['/api/v1/agents/me','/api/v1/agents/status','/api/v1/feed?sort=new&limit=15','/api/v1/posts/post-1/comments']
            assert all(row['authorization']=='Bearer '+API_KEY for row in calls)
            history=adapter.history();assert len(history)==4 and all(row['status']=='succeeded' for row in history);assert [row['detail']['items'] for row in history[:2]]==[1,1]
            assert API_KEY not in json.dumps(history)
        finally:await server.close()
    asyncio.run(run())


def test_feed_guards_and_read_failure_are_recorded(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        try:
            for args in ({'sort':'old','limit':10},{'sort':'new','limit':0},{'sort':'new','limit':51}):
                with pytest.raises(MoltbookError):await adapter.read('feed',**args)
            with pytest.raises(MoltbookError):await adapter.read('comments',post_id='../escape')
            assert calls==[] and adapter.history()==[]
        finally:await server.close()
    asyncio.run(run())


def test_explicitly_approved_post_preserves_pending_verification_and_replay(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        try:
            with pytest.raises(MoltbookError) as approval:await adapter.write(post_payload(),approved=False)
            assert approval.value.code=='approval_required' and calls==[] and adapter.history()==[]
            receipt=await adapter.write(post_payload(),approved=True)
            assert receipt['status']=='pending_verification'
            assert receipt['detail']['remote_id']=='remote-post'
            assert receipt['detail']['verification']=={'code':'verify-1','expires_at':'2030-01-01T00:00:00Z'}
            assert 'challenge' not in json.dumps(receipt)
            assert receipt['detail']['content_hash'] and 'Exact approved content.' not in json.dumps(receipt)
            replay=await adapter.write(post_payload(),approved=True);assert replay==receipt
            assert len(calls)==1 and calls[0]['method']=='POST' and calls[0]['path']=='/api/v1/posts'
            assert calls[0]['body']=={'submolt':'general','title':'A reviewed update','content':'Exact approved content.'}
            with pytest.raises(MoltbookError) as conflict:await adapter.write(post_payload(title='Changed'),approved=True)
            assert conflict.value.status==409 and len(calls)==1
            assert MoltbookAdapter(tmp_path,str(server.make_url('/api/v1')).rstrip('/')).history()==[receipt]
        finally:await server.close()
    asyncio.run(run())


def test_approved_comment_uses_current_parent_id_shape_and_published_receipt(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        try:
            receipt=await adapter.write(comment_payload(parent_id='comment-1'),approved=True)
            assert receipt['status']=='published' and receipt['detail']['remote_id']=='remote-comment'
            assert receipt['detail']['post_id']=='post-1' and receipt['detail']['parent_id']=='comment-1'
            assert calls[0]['path']=='/api/v1/posts/post-1/comments'
            assert calls[0]['body']=={'content':'A deliberate reply.','parent_id':'comment-1'}
            assert 'A deliberate reply.' not in adapter.path.read_bytes().decode(errors='ignore')
        finally:await server.close()
    asyncio.run(run())


def test_platform_rate_limit_is_failed_receipt_with_retry_and_no_false_publish(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        try:
            receipt=await adapter.write(post_payload(request_id='rate',title='Rate limited'),approved=True)
            assert receipt['status']=='failed';assert receipt['detail']['code']=='rate_limited';assert receipt['detail']['remote_status']==429;assert receipt['detail']['retry_after']==30
            assert 'remote_id' not in receipt['detail'] and len(calls)==1
            replay=await adapter.write(post_payload(request_id='rate',title='Rate limited'),approved=True);assert replay==receipt and len(calls)==1
        finally:await server.close()
    asyncio.run(run())


def test_dispatched_network_failure_is_durable_uncertain_and_never_retried(tmp_path):
    async def run():
        credential(tmp_path);adapter=MoltbookAdapter(tmp_path,'http://127.0.0.1:9/api/v1',timeout=.2);adapter.configure({'credential_ref':'moltbook-main'})
        receipt=await adapter.write(comment_payload(request_id='uncertain'),approved=True)
        assert receipt['status']=='uncertain';assert receipt['detail']['code']=='outcome_uncertain';assert receipt['detail']['remote_status']==503
        reopened=MoltbookAdapter(tmp_path,'http://127.0.0.1:9/api/v1',timeout=.2);assert await reopened.write(comment_payload(request_id='uncertain'),approved=True)==receipt
    asyncio.run(run())


def test_local_cooldown_stops_second_comment_before_network(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        try:
            first=await adapter.write(comment_payload(request_id='one'),approved=True);assert first['status']=='published'
            with pytest.raises(MoltbookError) as limited:await adapter.write(comment_payload(request_id='two'),approved=True)
            assert limited.value.code=='rate_limited' and limited.value.retry_after>0
            assert len(calls)==1 and len(adapter.history())==1
        finally:await server.close()
    asyncio.run(run())


def test_owner_http_and_native_tools_cross_real_protocol(tmp_path):
    async def run():
        adapter,server,calls=await service(tmp_path)
        @web.middleware
        async def owner(request,handler):request['user']='owner';return await handler(request)
        app=web.Application(middlewares=[owner]);app['moltbook_factory']=lambda:adapter;register(app)
        try:
            async with TestClient(TestServer(app)) as client:
                config=await client.get(PREFIX+'/config');assert config.status==200;assert (await config.json())['credential_ref']=='moltbook-main'
                feed=await client.get(PREFIX+'/feed?sort=new&limit=15');assert feed.status==200;assert (await feed.json())['posts'][0]['title']=='Protocol news'
                denied=await client.post(PREFIX+'/post',json={**post_payload(request_id='http-denied'),'approved':False});assert denied.status==403
                posted=await client.post(PREFIX+'/post',json={**post_payload(request_id='http-write'),'approved':True});assert posted.status==202;assert (await posted.json())['status']=='pending_verification'
                history=await client.get(PREFIX+'/history');assert history.status==200;assert len((await history.json())['items'])==2
            tools=MoltbookTools(adapter);definitions={item.name:item for item in await tools.list_tools()};assert not definitions['moltbook_read'].requires_approval;assert definitions['moltbook_write'].requires_approval;assert definitions['moltbook_write'].risk_level.value=='caution'
            profile=await tools.invoke('moltbook_read',{'action':'profile'});assert profile.success and json.loads(profile.output)['id']=='agent-1'
            comment=await tools.invoke('moltbook_write',comment_payload(request_id='native-comment'));assert comment.success and json.loads(comment.output)['status']=='published'
            history=await tools.invoke('moltbook_history',{});assert history.success and len(json.loads(history.output)['items'])==4
            invalid=await tools.invoke('wrong',{});assert not invalid.success
            manifest=json.loads(Path('runtime/gideon/extensions/apps/native/gideon-moltbook/app.json').read_text());assert manifest['name']=='gideon-moltbook';assert manifest['provider']['implementation']=='gideon.workspace.capabilities.experience.moltbook_tools:create_provider'
        finally:await server.close()
    asyncio.run(run())


def test_dashboard_rejects_app_and_anonymous_callers(tmp_path):
    async def run():
        adapter=MoltbookAdapter(tmp_path)
        app=web.Application();app['moltbook_factory']=lambda:adapter;register(app)
        async with TestClient(TestServer(app)) as client:assert (await client.get(PREFIX+'/config')).status==403
        @web.middleware
        async def app_identity(request,handler):request['user']='owner';request['app']='embedded';return await handler(request)
        embedded=web.Application(middlewares=[app_identity]);embedded['moltbook_factory']=lambda:adapter;register(embedded)
        async with TestClient(TestServer(embedded)) as client:assert (await client.get(PREFIX+'/config')).status==403
    asyncio.run(run())
