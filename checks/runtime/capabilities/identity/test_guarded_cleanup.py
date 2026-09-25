from test_guarded_recipes import run


def test_mode_revocation_allows_owner_cancellation_and_restart_keeps_receipt(tmp_path):
    run(tmp_path, '''
from gideon.engine import session_restrictions
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.core.config import AppConfig
service,runtime,state,workspace,model=await fixture(home)
recipe,started=await create(service,[{'id':'write','tool':'write_file','arguments':{'path':'restricted.txt','content':'Never written'}}])
await service.advance(run_id=started['id'],expected_index=0)
pending=await wait(service,started['id'],'waiting_approval')
entry=state.sessions._sessions['dashboard:recipes']
assert entry.semaphore.locked()
session_restrictions.mark_temporary('dashboard:recipes')
try:
 service.get_run(started['id'])
 raise AssertionError('restricted session read run outputs')
except ValueError:
 pass
cancelled=await service.cancel(run_id=started['id'])
assert cancelled['status']=='cancelled'
assert cancelled['steps']==[]
assert cancelled['permission'] is None
for _ in range(500):
 if not entry.semaphore.locked():
  break
 await asyncio.sleep(.01)
assert not entry.semaphore.locked()
assert not (workspace/'restricted.txt').exists()
session_restrictions.clear('dashboard:recipes')
restarted=GuardedRecipes(home,ConsoleState(ConversationDirectory(AppConfig()),0))
assert restarted.get_run(started['id'])['status']=='cancelled'
assert restarted.get_run(started['id'])['steps'][0]['status']=='failed'
assert (await restarted.cancel(run_id=started['id']))['status']=='cancelled'
assert runtime._messages[-1]['role']=='tool'
await model.shutdown()
''')
