from test_lifecycle import run


def test_concurrent_different_loop_bindings_serialize_before_engine_effects(tmp_path):
    run(tmp_path, '''
await manager.start(state,service,loop.id)
await manager.start(state,service,other.id)
assert loops.get(loop.id).status==LoopStatus.RUNNING
assert loops.get(other.id).status==LoopStatus.RUNNING
await service._lock.acquire()
first=asyncio.create_task(store.configure(loop_id=loop.id,enabled=False,expected_revision=0,request_id='first',state=state,service=service))
await asyncio.sleep(0)
assert not first.done()
second=asyncio.create_task(store.configure(loop_id=other.id,enabled=False,expected_revision=0,request_id='second',state=state,service=service))
await asyncio.sleep(0)
assert not second.done()
assert store.policy()['revision']==0
assert len(store.status()['journal'])==1
assert store.status()['journal'][0]['request_id']=='first'
service._lock.release()
receipt=await first
assert receipt['status']=='applied'
try:
 await second
 raise AssertionError('second identity binding accepted')
except ValueError as error:
 assert 'another loop' in str(error)
assert store.policy()['loop_id']==loop.id
assert store.policy()['revision']==1
assert not store.policy()['enabled']
assert loops.get(loop.id).status==LoopStatus.PAUSED
assert loops.get(other.id).status==LoopStatus.RUNNING
assert service.get_by_session(manager.session_key(other.id)).active
assert nudge_allowed(manager.session_key(other.id))
assert len(store.status()['journal'])==1
assert await configure(False,0,'first')==receipt
assert LifecycleStore(home).policy()==store.policy()
''')
