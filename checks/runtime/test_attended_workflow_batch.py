"""A native batch retains its displayed wait and consumes one owner start Allow."""
import asyncio
import time
from types import SimpleNamespace
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.automation.workflows import batch_start, handlers, store
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.subagent import DelegationSupervisor
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.sessions import api_approval_resolve
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, MIXED_INTERNAL_ROUTES
from gideon.security import session_credentials
from gideon.security.approval_answer import YOU, principal_record
from gideon.cognition.history import ConversationLog

@pytest.mark.asyncio
async def test_authenticated_batch_restores_question_and_starts_two_leaves_on_one_owner_answer(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path/'home'))
    monkeypatch.setenv('GIDEON_AUTH_MODE', 'local-token')
    monkeypatch.setattr('gideon.extensions.providers.provider_bridge.can_resolve_use_case', lambda _: True)
    sessions = ConversationDirectory(AppConfig())
    launched = []
    async def spawn_ask(*args):
        raise AssertionError('batch leaf must consume the batch Allow, not ask again')
    manager = DelegationSupervisor(sessions, None, on_spawn_approval=spawn_ask,
        validate_batch_start_approval=batch_start.validate_start_approval)
    async def leaf_run(info):
        launched.append(info)
        info.result = 'done'
        info.done = True
        manager._dec_running(info)
    monkeypatch.setattr(manager, '_run', leaf_run)
    # Exercise actual manager's memory/incident/budget admission; only model execution is substituted.
    log = ConversationLog(tmp_path/'sessions')
    log.append('batchchat', 'user', 'Please run these two tasks')
    log.update_metadata('batchchat', {'initiator': principal_record(YOU), 'memory_mode':'persistent', 'lifecycle':'active'})
    state = ConsoleState(sessions, time.time(), subagents=manager, conversation_log=log)
    supervisor = WorkflowWatchdog(services=EngineServices(subagents=manager))
    state.workflows = supervisor
    proof = session_credentials.begin_turn('dashboard:batchchat', YOU, turn_id='batch-turn', origin_session_key='batchchat', memory_mode='persistent')
    app = web.Application(middlewares=[token_auth_middleware(port=0, internal_secret='batch-secret', mixed_internal_routes=MIXED_INTERNAL_ROUTES)])
    app['local_secret'] = 'batch-secret'; app['state'] = state
    app.router.add_post('/api/workflows/batches', handlers.api_batch_start)
    app.router.add_get('/api/workflows/batches/{name}', handlers.api_batch_status)
    app.router.add_post('/api/approvals/{id}/{action}', api_approval_resolve)
    owner = {'Authorization':'Bearer ' + generate_token('batch-owner', kind='browser')}
    internal = {'X-Internal-Secret':'batch-secret', 'X-Session-Key':proof.work.session_key, 'X-Session-Proof':proof.bearer}
    spec = {'name':'native-waiting-batch', 'root':{'kind':'parallel','id':'batch','config':{'join':'all'},'children':[
        {'kind':'stage','id':name,'config':{'prompt':'Perform the displayed task '+name, 'capability':'mutating','approval_mode':'ask'}} for name in ('first','second')]}}
    try:
        async with TestClient(TestServer(app)) as client:
            reply = await client.post('/api/workflows/batches', json={'run_once':spec}, headers=internal)
            body = await reply.json(); assert reply.status == 202, body
            assert body['status'] == 'awaiting_approval' and not launched
            for _ in range(100):
                if state._pending_approvals: break
                await asyncio.sleep(.01)
            entry = state._pending_approvals[body['approval']]
            old_revision = entry['revision']
            assert entry['owner_only'] and 'first' in entry['tool_input'] and 'second' in entry['tool_input']
            # Actual shutdown cancellation leaves only the sealed unanswered record.
            for task in list(state._workflow_batch_tasks.values()): task.cancel()
            await asyncio.gather(*list(state._workflow_batch_tasks.values()), return_exceptions=True)
            assert batch_start.read(body['batch'])['status'] == 'awaiting_approval'
            await batch_start.restore(state, supervisor)
            for _ in range(100):
                if body['approval'] in state._pending_approvals: break
                await asyncio.sleep(.01)
            entry = state._pending_approvals[body['approval']]
            assert entry['revision'] != old_revision and not launched
            denied = await client.post('/api/approvals/'+body['approval']+'/approve', json={'expected_revision':entry['revision']}, headers=internal)
            assert denied.status == 403
            approved = await client.post('/api/approvals/'+body['approval']+'/approve', json={'expected_revision':entry['revision']}, headers=owner)
            assert approved.status == 200, await approved.text()
            for _ in range(300):
                row = batch_start.read(body['batch'])
                if row.get('run_id'): break
                await asyncio.sleep(.01)
            assert row.get('run_id'), row
            controller = supervisor.controller(row['run_id'])
            await controller.wait_for_terminal(timeout=5)
            assert len(launched) == 2
            assert all(info.batch_start_approval and info.approval_mode == 'ask' for info in launched)
            assert store.read_spec(row['run_id'])['root']['children'][0]['config']['prompt'] == spec['root']['children'][0]['config']['prompt']
            assert len(state._pending_approvals) == 0
            second = await client.post('/api/approvals/'+body['approval']+'/approve', json={'expected_revision':entry['revision']}, headers=owner)
            assert second.status == 404
    finally:
        session_credentials.end_turn(proof)
        for task in list(getattr(state,'_workflow_batch_tasks',{}).values()): task.cancel()
        await supervisor.stop()
