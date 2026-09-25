import os
import subprocess
import sys
import textwrap
from pathlib import Path

PRELUDE = '''
import asyncio,json,sqlite3
from pathlib import Path
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.recipes import RecipeStore
from gideon.workspace.capabilities.identity.store import StoryStore,ConflictError
from gideon.workspace.capabilities.identity.guarded_recipes import GuardedRecipes
from gideon.integrations.tool_providers import tool_prefs
from guarded_fixture import fixture
home=config_dir()
async def wait(service,id,*statuses):
 for _ in range(500):
  run=service.get_run(id)
  if run['status'] in statuses:
   return run
  await asyncio.sleep(.01)
 raise AssertionError('Run did not reach expected state: '+str(run))
async def create(service,steps,key='recipe'):
 recipe=await service.save(session_key='dashboard:recipes',title='Guarded work',steps=steps,request_id=key)
 run=await service.begin(session_key='dashboard:recipes',recipe_id=recipe['id'],revision=recipe['revision'],request_id=key+'-run')
 return recipe,run
async def decide(service,run,decision='approve'):
 return await service.decide(run_id=run['id'],request_id=run['permission']['request_id'],decision=decision,session_key='dashboard:recipes')
'''


def run(home, body):
    script = PRELUDE + '\nasync def main():\n' + textwrap.indent(textwrap.dedent(body), ' ') + '\nasyncio.run(main())\n'
    fixture_dir = str(Path(__file__).parent)
    result = subprocess.run([sys.executable, '-c', script], env={**os.environ, 'GIDEON_HOME': str(home), 'PYTHONPATH': fixture_dir + os.pathsep + os.environ.get('PYTHONPATH', '')}, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_cross_provider_actions_require_actual_approval_and_pair_session_history(tmp_path):
    run(tmp_path, '''
service,runtime,state,workspace,model=await fixture(home)
catalog=await service.catalog('dashboard:recipes')
assert any(row['name']=='write_file' and row['requires_approval'] for row in catalog['tools'])
assert any(row['name']=='identity_story_create' for row in catalog['tools'])
assert not any(row['name'].startswith('identity_guarded_') for row in catalog['tools'])
recipe,started=await create(service,[{'id':'write','tool':'write_file','arguments':{'path':'result.txt','content':'Actual guarded file'}},{'id':'story','tool':'identity_story_create','arguments':{'prompt':'What happened?','theme':'work','text':'Recorded after actual file','request_id':'story'}}])
assert started['session_key']=='dashboard:recipes'
assert len(started['tool_fingerprints'])==2
await service.advance(run_id=started['id'],expected_index=0)
pending=await wait(service,started['id'],'waiting_approval')
assert not (workspace/'result.txt').exists()
assert pending['permission']['tool']=='write_file'
assert pending['permission']['arguments']['content']=='Actual guarded file'
assert state.sessions._sessions['dashboard:recipes'].semaphore.locked()
await decide(service,pending)
first=await wait(service,started['id'],'ready')
assert (workspace/'result.txt').read_text()=='Actual guarded file'
assert first['next_index']==1
assert first['steps'][0]['status']=='completed'
assert not state.sessions._sessions['dashboard:recipes'].semaphore.locked()
assert await service.advance(run_id=started['id'],expected_index=0)==first
await service.advance(run_id=started['id'],expected_index=1)
pending=await wait(service,started['id'],'waiting_approval')
assert pending['permission']['tool']=='identity_story_create'
assert StoryStore(home/'capabilities/identity/stories.sqlite3').list()==[]
await decide(service,pending)
finished=await wait(service,started['id'],'completed')
assert finished['next_index']==2
assert len(finished['steps'])==2
assert StoryStore(home/'capabilities/identity/stories.sqlite3').list()[0]['text']=='Recorded after actual file'
assert [message['role'] for message in runtime._messages]==['assistant','tool','assistant','tool']
assert runtime._messages[0]['tool_calls'][0]['id']==runtime._messages[1]['tool_call_id']
assert runtime._messages[2]['tool_calls'][0]['id']==runtime._messages[3]['tool_call_id']
assert runtime._model is model
assert not state.sessions._sessions['dashboard:recipes'].semaphore.locked()
assert GuardedRecipes(home,state).get_run(started['id'])==finished
await model.shutdown()
''')


def test_reject_cancel_busy_lease_and_wrong_session_decisions_do_not_write(tmp_path):
    run(tmp_path, '''
service,runtime,state,workspace,model=await fixture(home)
recipe,started=await create(service,[{'id':'write','tool':'write_file','arguments':{'path':'refused.txt','content':'Never written'}}])
entry=state.sessions._sessions['dashboard:recipes']
await entry.semaphore.acquire()
deferred=await service.advance(run_id=started['id'],expected_index=0)
assert deferred['status']=='deferred'
assert 'busy' in deferred['defer_reason']
assert runtime._messages==[]
assert not (workspace/'refused.txt').exists()
entry.semaphore.release()
await service.advance(run_id=started['id'],expected_index=0)
pending=await wait(service,started['id'],'waiting_approval')
try:
 await service.decide(run_id=started['id'],request_id=pending['permission']['request_id'],decision='approve',session_key='dashboard:other')
 raise AssertionError('wrong session approved')
except ConflictError:
 pass
assert entry.semaphore.locked()
await decide(service,pending,'reject')
rejected=await wait(service,started['id'],'failed')
assert 'declined' in rejected['steps'][0]['error']
assert not (workspace/'refused.txt').exists()
assert not entry.semaphore.locked()
second=await service.begin(session_key='dashboard:recipes',recipe_id=recipe['id'],revision=1,request_id='second-run')
await service.advance(run_id=second['id'],expected_index=0)
pending=await wait(service,second['id'],'waiting_approval')
assert (await service.cancel(run_id=second['id']))['status']=='cancelled'
for _ in range(500):
 if not entry.semaphore.locked():
  break
 await asyncio.sleep(.01)
assert not entry.semaphore.locked()
assert service.get_run(second['id'])['status']=='cancelled'
assert not (workspace/'refused.txt').exists()
assert service.get_run(second['id'])['steps'][0]['status']=='failed'
await model.shutdown()
''')


def test_existing_policy_denial_and_live_preference_revocation_are_enforced(tmp_path):
    run(tmp_path, '''
service,runtime,state,workspace,model=await fixture(home)
recipe,started=await create(service,[{'id':'write','tool':'write_file','arguments':{'path':'denied.txt','content':'Never written'}}])
runtime._extra_deny.append('write_file')
await service.advance(run_id=started['id'],expected_index=0)
denied=await wait(service,started['id'],'failed')
assert not denied.get('permission')
assert not (workspace/'denied.txt').exists()
assert denied['steps'][0]['status']=='failed'
runtime._extra_deny.clear()
second=await service.begin(session_key='dashboard:recipes',recipe_id=recipe['id'],revision=1,request_id='again')
await service.advance(run_id=second['id'],expected_index=0)
pending=await wait(service,second['id'],'waiting_approval')
runtime._extra_deny.append('write_file')
try:
 await decide(service,pending)
 raise AssertionError('revoked policy approved')
except ValueError:
 pass
assert (await wait(service,second['id'],'failed'))['steps'][0]['status']=='failed'
assert not (workspace/'denied.txt').exists()
runtime._extra_deny.clear()
third_recipe,third=await create(service,[{'id':'story','tool':'identity_story_create','arguments':{'prompt':'Question','theme':'test','text':'Not admitted','request_id':'revoked'}}],key='identity')
assert tool_prefs.set_enabled('gideon-identity','identity_story_create',False)['ok']
try:
 await service.advance(run_id=third['id'],expected_index=0)
 raise AssertionError('disabled provider tool executed')
except ValueError as error:
 assert 'permissions' in str(error)
assert not state.sessions._sessions['dashboard:recipes'].semaphore.locked()
assert StoryStore(home/'capabilities/identity/stories.sqlite3').list()==[]
await model.shutdown()
''')


def test_legacy_adapter_cannot_bypass_guarded_approval_or_read_bound_outputs(tmp_path):
    run(tmp_path, '''
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
service,runtime,state,workspace,model=await fixture(home)
recipe,started=await create(service,[{'id':'story','tool':'identity_story_create','arguments':{'prompt':'Question','theme':'test','text':'Private output','request_id':'bound'}}])
legacy=RecipeStore(service.store.path)
assert legacy.list_runs()==[]
for operation in (lambda:legacy.get_run(started['id']),lambda:legacy.cancel(started['id']),lambda:legacy.begin(recipe_id=recipe['id'],revision=1,request_id='recipe-run')):
 try:
  operation()
  raise AssertionError('legacy adapter accessed bound run')
 except ValueError as error:
  assert 'guarded' in str(error)
try:
 await legacy.advance(started['id'],0,IdentityToolProvider(home))
 raise AssertionError('legacy direct dispatch bypassed approval')
except ValueError as error:
 assert 'guarded' in str(error)
unbound=legacy.begin(recipe_id=recipe['id'],revision=1,request_id='legacy-attempt')
try:
 await legacy.advance(unbound['id'],0,IdentityToolProvider(home))
 raise AssertionError('unbound legacy write bypassed approval')
except ValueError as error:
 assert 'not admitted' in str(error)
assert StoryStore(home/'capabilities/identity/stories.sqlite3').list()==[]
assert runtime._messages==[]
assert service.get_run(started['id'])['status']=='ready'
await model.shutdown()
''')


def test_dry_run_foreign_home_and_unavailable_sessions_never_claim_execution(tmp_path):
    run(tmp_path, '''
service,runtime,state,workspace,model=await fixture(home,dry_run=True)
try:
 await service.catalog('dashboard:recipes')
 raise AssertionError('dry run admitted')
except ValueError as error:
 assert 'Observe-only' in str(error)
for key in ('telegram:remote','dashboard:absent'):
 try:
  await service.catalog(key)
  raise AssertionError('unavailable session admitted')
 except ValueError:
  pass
foreign=GuardedRecipes(home/'other',state)
try:
 await foreign.catalog('dashboard:recipes')
 raise AssertionError('foreign home admitted')
except ValueError as error:
 assert 'different active home' in str(error)
assert runtime._messages==[]
assert list(workspace.iterdir())==[]
assert service.store.list_runs()==[]
await model.shutdown()
''')
