"""Explicit signed trigger tools intersect real native memory authority."""
import time
from types import SimpleNamespace
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from checks.hypermid.test_memory_release import _start_daemon, _open_memory, _draft, _mutation
from gideon.hypermid.foundation import Scope, Id
from gideon.hypermid.contracts import RecordKind, MemoryOperation
from gideon.hypermid.memory import HypermidMemoryProvider
from gideon.cognition.memory import MemoryJournal
from gideon.automation.triggers import grants
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import defs, store
from gideon.automation.workflows.models import WorkflowRun, RunStatus
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.interfaces.dashboard.handlers.schedule import api_lessons, api_lessons_delete
from gideon.security.approval_answer import YOU
from gideon.security.durable_work import accepted_trigger_origin, bind_run_origin, workflow_work
from gideon.security.session_credentials import credential_for, current_work, memory_reach
from test_trigger_completion_lifecycle import one_shot

@pytest.mark.asyncio
async def test_actual_native_signed_tool_allow_undeclared_revoke_wrong_scope(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path/'home'))
    scope = Scope('tool-owner', 'tool-project', 'tool-workspace')
    cap = Id('tool-cap')
    daemon = _start_daemon(tmp_path, 'daemon', scope, cap, {Id('memory-records'), Id('memory-list'), Id('lesson.approved')})
    native = HypermidMemoryProvider(daemon.record, scope=scope, capability_id=cap)
    client = None
    previous = defs.get_provider('native')
    definitions = NativeWorkflowDefProvider()
    defs.register_provider(definitions)
    try:
        client, memory = await _open_memory(daemon, scope, cap)
        await memory.create(_mutation(scope, MemoryOperation.CREATE, Id('lesson.approved'), 'create'), _draft(scope, Id('lesson.approved'), RecordKind.FACT, 'approved lesson', metadata={'gideon_kind':'lesson','value':{'rule':'approved lesson','category':'knowledge','negative':''}}), now_ms=int(time.time()*1000))
        native.init()
        journal = MemoryJournal(workspace=tmp_path/'journal')
        journal.init()
        journal.vector_store = native
        state = ConsoleState(None, time.time(), context_builder=SimpleNamespace(memory=journal))
        await definitions.save_def(name='tool-work', root={'kind':'sequence', 'children':[]}, provenance='user', _owner_saved=True)
        trigger = one_shot('clock:tool-grant', time.time())
        trigger.workflow = {'provider':'run-workflow','config':{'workflow':'tool-work'}}
        trigger.capabilities['tools'] = ['memory_list']
        question = grants.question(trigger)
        assert grants.grant(trigger, confirmed_revision=question.revision, principal=YOU, shown=question.shown)
        TriggerStore().upsert(trigger)
        run = store.create(WorkflowRun(id='', workflow_name='tool-work', status=RunStatus.DRAFT))
        assert bind_run_origin(run, accepted_trigger_origin(trigger.id))
        store.save(run)
        secret = 'isolated-test-only'
        app = web.Application(middlewares=[token_auth_middleware(internal_routes=frozenset({'GET /api/lessons','DELETE /api/lessons'}), internal_secret=secret)])
        app['state'] = state
        app['local_secret'] = secret
        app.router.add_get('/api/lessons', api_lessons)
        app.router.add_delete('/api/lessons', api_lessons_delete)
        with workflow_work(run.id, 'tools'):
            work = current_work()
            assert not memory_reach(scope).read_allowed
            headers={'X-Internal-Secret':secret,'X-Session-Key':work.session_key,'X-Session-Proof':credential_for(work.session_key)}
            async with TestClient(TestServer(app)) as http:
                allowed = await http.get('/api/lessons', headers=headers)
                body = await allowed.json()
                assert allowed.status == 200 and body['lessons'], body
                denied = await http.delete('/api/lessons', headers=headers, json={'rule':'approved'})
                assert denied.status == 403
                # Same reviewed operation cannot borrow authority from another native scope.
                wrong = HypermidMemoryProvider(daemon.record, scope=scope, capability_id=cap)
                wrong.init()
                # Keep the real authenticated actor transport; the requested memory scope must still be checked by native RPC authority.
                wrong.scope = Scope('other-owner','tool-project','tool-workspace')
                journal.vector_store = wrong
                refused = await http.get('/api/lessons', headers=headers)
                wrong_body = await refused.json() if refused.status == 200 else {}
                assert refused.status == 403 and not wrong_body.get('lessons')
                wrong.close()
                journal.vector_store = native
                grants.revoke(trigger.id)
                revoked = await http.get('/api/lessons', headers=headers)
                assert revoked.status == 403
            assert not memory_reach(scope).background_allowed
    finally:
        native.close()
        if client is not None:
            await client.close()
        daemon.stop()
        if previous is None:
            defs.unregister_provider('native')
        else:
            defs.register_provider(previous)
