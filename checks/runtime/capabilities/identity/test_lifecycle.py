import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest

PRELUDE = '''
import asyncio,json,time,sqlite3
from pathlib import Path
from gideon.core.config import config_dir,AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.automation.loop import store as loops,manager
from gideon.automation.loop.loop import Loop,LoopStatus
from gideon.automation.triggers.nudge import AutoNudgeService,NudgeAttempt
from gideon.workspace.capabilities.identity.lifecycle import LifecycleStore,require_loop_allowed,nudge_allowed
from gideon.workspace.capabilities.identity.lifecycle_route import route_fingerprint
from gideon.workspace.capabilities.identity.store import ConflictError
home=config_dir()
store=LifecycleStore(home)
loop=loops.create(Loop(id='',name='Identity research',kind='general',task='Review current anchors',agent='default'))
other=loops.create(Loop(id='',name='Unrelated research',kind='general',task='Unrelated work',agent='default'))
service=AutoNudgeService(base_dir=home)
state=ConsoleState(ConversationDirectory(AppConfig()),time.time())
async def configure(enabled=True,revision=0,key='enable'):
 return await store.configure(loop_id=loop.id,enabled=enabled,expected_revision=revision,request_id=key,state=state,service=service)
async def action(name,revision=1,key=None):
 return await store.action(action=name,expected_revision=revision,request_id=key or name,state=state,service=service)
'''


def run(home, body):
    program = PRELUDE + '\nasync def main():\n' + textwrap.indent(textwrap.dedent(body), ' ') + '\nasyncio.run(main())\n'
    result = subprocess.run([sys.executable, '-c', program], env={**os.environ, 'GIDEON_HOME': str(home)}, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_loop_start_pause_resume_stop_and_replay(tmp_path):
    run(tmp_path, '''
assert store.status()['policy']['revision']==0
assert store.status()['loop'] is None
assert store.status()['provider_readiness']=='unknown'
assert store.status()['requests']==[]
assert nudge_allowed('ordinary-session')
receipt=await configure()
assert receipt['status']=='applied'
assert receipt['policy']['enabled']
assert receipt['policy']['loop_id']==loop.id
assert await configure()==receipt
assert len(store.status()['journal'])==1
assert store.status()['loop']['status']=='ready'
started=await action('start')
assert started['loop_status']=='running'
assert loops.get(loop.id).status==LoopStatus.RUNNING
trigger=service.get_by_session(manager.session_key(loop.id))
assert trigger.active
assert trigger.session_name==manager.session_key(loop.id)
assert await action('start')==started
assert len(service.list_all())==1
assert state.get_session(manager.session_key(loop.id)) is not None
parallel=await service.add(session_name=manager.session_key(loop.id)+'-worker',message='Existing task',idle_secs=60)
assert nudge_allowed(parallel.session_name)
paused=await action('pause')
assert paused['loop_status']=='paused'
assert not service.get_by_session(trigger.session_name).active
assert not service.get_by_session(parallel.session_name).active
assert not nudge_allowed(trigger.session_name)
assert nudge_allowed(manager.session_key(other.id))
resumed=await action('resume')
assert resumed['loop_status']=='running'
assert service.get_by_session(trigger.session_name).active
cancelled=await action('cancel_turn')
assert cancelled['cancellation']=='idle'
assert loops.get(loop.id).status==LoopStatus.RUNNING
stopped=await action('stop')
assert stopped['loop_status']=='stopped'
assert service.get_by_session(trigger.session_name) is None
assert not service.get_by_session(parallel.session_name).active
assert not nudge_allowed(parallel.session_name)
assert loops.get(other.id).status==LoopStatus.READY
assert LifecycleStore(home).status()==store.status()
assert store.status()['provider_readiness']=='unknown'
''')


def test_disabled_and_route_revocation_guard_actual_manager_and_nudge(tmp_path):
    run(tmp_path, '''
await configure()
await action('start')
trigger=service.get_by_session(manager.session_key(loop.id))
disabled=await configure(False,1,'disable')
assert disabled['policy']['revision']==2
assert not disabled['policy']['enabled']
assert loops.get(loop.id).status==LoopStatus.PAUSED
assert not service.get_by_session(trigger.session_name).active
try:
 await manager.start(state,service,loop.id)
 raise AssertionError('disabled bound loop started')
except ValueError as error:
 assert 'disabled' in str(error)
assert (await NudgeAttempt(service,trigger,time.time()).run())==(False,'identity_policy_blocked')
await manager.start(state,service,other.id)
assert loops.get(other.id).status==LoopStatus.RUNNING
assert service.get_by_session(manager.session_key(other.id)).active
await configure(True,2,'reapprove')
await action('resume',3,'resume-approved')
assert nudge_allowed(trigger.session_name)
(home/'routing_policy.json').write_text('{"policy":"changed"}')
assert not nudge_allowed(trigger.session_name)
assert nudge_allowed(manager.session_key(other.id))
try:
 await manager.start(state,service,loop.id)
 raise AssertionError('changed route started')
except ValueError as error:
 assert 'route changed' in str(error)
assert (await NudgeAttempt(service,trigger,time.time()).run())==(False,'identity_policy_blocked')
await configure(True,3,'new-route-approval')
assert nudge_allowed(trigger.session_name)
assert store.policy()['revision']==4
assert store.policy()['route_fingerprint']==route_fingerprint(loops.get(loop.id))
assert loops.get(other.id).status==LoopStatus.RUNNING
''')


def test_thinking_claim_replay_quota_and_real_unavailable_delivery(tmp_path):
    run(tmp_path, '''
await configure()
await action('start')
request=store.request_thinking(request_id='reflect-first',preset='reflect')
assert request['status']=='pending'
assert request['policy_revision']==1
assert store.request_thinking(request_id='reflect-first',preset='reflect')==request
try:
 store.request_thinking(request_id='reflect-first',preset='review')
 raise AssertionError('preset mismatch replayed')
except ConflictError:
 pass
try:
 store.request_thinking(request_id='second',preset='review')
 raise AssertionError('quota not enforced')
except ConflictError as error:
 assert 'quota' in str(error)
trigger=service.get_by_session(manager.session_key(loop.id))
before_message=trigger.message
receipt=await store.dispatch(id=request['id'],state=state,service=service)
assert receipt['status']=='blocked'
assert receipt['reason']=='no_deliverer'
assert 'completion' in receipt['meaning']
assert await store.dispatch(id=request['id'],state=state,service=service)==receipt
assert len(store.status()['requests'])==1
assert service.get_by_session(trigger.session_name).message==before_message
assert service.get_by_session(trigger.session_name).cycle_count==0
assert store.status()['provider_readiness']=='unknown'
try:
 store.request_thinking(request_id='too-soon',preset='review')
 raise AssertionError('separation quota bypassed')
except ConflictError:
 pass
assert LifecycleStore(home).status()['requests']==[receipt]
''')


def test_pending_approval_revocation_and_interrupted_claim_are_honest(tmp_path):
    run(tmp_path, '''
await configure()
await action('start')
request=store.request_thinking(request_id='pending',preset='review')
await configure(True,1,'reapprove')
receipt=await store.dispatch(id=request['id'],state=state,service=service)
assert receipt['status']=='blocked'
assert receipt['reason']=='Thinking request approval changed'
assert service.get_by_session(manager.session_key(loop.id)).cycle_count==0
with sqlite3.connect(store.path) as db:
 row=dict(receipt,status='running')
 db.execute('UPDATE requests SET body=? WHERE id=?',(json.dumps(row),row['id']))
assert await store.dispatch(id=row['id'],state=state,service=service)==row
assert store.status()['requests'][0]['status']=='running'
assert store.status()['provider_readiness']=='unknown'
try:
 await store.dispatch(id='absent',state=state,service=service)
 raise AssertionError('missing request accepted')
except KeyError:
 pass
''')


def test_invalid_home_revision_route_and_unavailable_runtime(tmp_path):
    run(tmp_path, '''
foreign=LifecycleStore(home/'other')
try:
 foreign.status()
 raise AssertionError('foreign engine projected')
except ValueError as error:
 assert 'different active home' in str(error)
assert store.policy()['revision']==0
try:
 await store.configure(loop_id=loop.id,enabled=True,expected_revision=0,request_id='absent',state=None,service=None)
 raise AssertionError('absent engine accepted')
except ValueError as error:
 assert 'unavailable' in str(error)
assert store.status()['journal']==[]
try:
 await configure(revision=4)
 raise AssertionError('stale revision accepted')
except ConflictError:
 pass
assert store.status()['journal']==[]
await configure()
try:
 await store.configure(loop_id=other.id,enabled=True,expected_revision=1,request_id='other',state=state,service=service)
 raise AssertionError('binding switched')
except ValueError as error:
 assert 'another loop' in str(error)
try:
 await configure(False,1,'enable')
 raise AssertionError('mismatch replay accepted')
except ConflictError:
 pass
try:
 await action('resume')
 raise AssertionError('invalid transition accepted')
except ValueError as error:
 assert 'current loop state' in str(error)
assert store.status()['journal'][-1]['status']=='failed'
assert loops.get(loop.id).status==LoopStatus.READY
try:
 store.request_thinking(request_id='bad',preset='arbitrary')
 raise AssertionError('unbounded preset accepted')
except ValueError:
 pass
assert store.status()['requests']==[]
''')


def test_actual_http_native_and_privacy_contract(tmp_path):
    run(tmp_path, '''
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from gideon.interfaces.dashboard.handlers.capabilities_identity_lifecycle import register,PREFIX
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.integrations.mcp_core import set_current_session_key,reset_current_session_key
from gideon.integrations.inbox_providers.native_source import set_dashboard_state
await service.start()
set_dashboard_state(state)
app=web.Application()
app['state']=state
register(app,home=home)
async with TestClient(TestServer(app)) as client:
 response=await client.get(PREFIX)
 assert response.status==200
 assert (await response.json())['policy']['revision']==0
 response=await client.post(PREFIX+'/configure',json={'loop_id':loop.id,'enabled':True,'expected_revision':0,'request_id':'http'})
 assert response.status==200
 assert (await response.json())['status']=='applied'
 response=await client.post(PREFIX+'/action',json={'action':'start','expected_revision':1,'request_id':'http-start'})
 assert response.status==200
 assert (await response.json())['loop_status']=='running'
 for field in ('home','provider','state','service'):
  response=await client.post(PREFIX+'/requests',json={'preset':'reflect','request_id':'override-'+field,field:'injected'})
  assert response.status==400
 assert store.status()['requests']==[]
 provider=IdentityToolProvider(home)
 token=set_current_session_key('dashboard:identity-lifecycle')
 try:
  result=await provider.invoke('identity_lifecycle_status',{})
  assert result.success
  assert json.loads(result.output)['loop']['status']=='running'
  denied=await provider.invoke('identity_lifecycle_configure',{})
  assert not denied.success
  denied=await provider.invoke('identity_lifecycle_request_thinking',{'preset':'arbitrary','request_id':'bad'})
  assert not denied.success
  result=await provider.invoke('identity_lifecycle_request_thinking',{'preset':'reflect','request_id':'native'})
  assert result.success
  request=json.loads(result.output)
  result=await provider.invoke('identity_lifecycle_dispatch',{'id':request['id']})
  assert result.success
  assert json.loads(result.output)['reason']=='no_deliverer'
 finally:
  reset_current_session_key(token)
 token=set_current_session_key('telegram:external')
 try:
  result=await provider.invoke('identity_lifecycle_status',{})
  assert not result.success
  assert 'private conversation' in result.error
 finally:
  reset_current_session_key(token)
 response=await client.post(PREFIX+'/action',json={'action':'pause','expected_revision':99,'request_id':'stale'})
 assert response.status==409
 assert loops.get(loop.id).status==LoopStatus.RUNNING
 response=await client.get(PREFIX)
 assert response.headers['Cache-Control']=='no-store'
 assert len((await response.json())['requests'])==1
service.stop()
set_dashboard_state(None)
''')
