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
async def test_real_private_batch_restores_typed_scope_and_completes_two_sdk_leaves(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path/'home'))
    monkeypatch.setenv('GIDEON_AUTH_MODE', 'local-token')
    from test_background_completion_contract import configured_completion
    from gideon.extensions.providers.provider_bridge import resolve_provider_for_use_case
    from gideon.extensions.providers.use_cases import save_active_models
    from gideon.security.execution_lineage import host_runtime_admission
    from gideon.security.session_credentials import bind_execution
    from gideon.cognition.context import PromptAssembler
    from gideon.hypermid.memory import HypermidMemoryProvider
    from gideon.hypermid.foundation import Scope, Id
    from gideon.engine.delegation_host import DelegationHost
    import subprocess, os, logging
    from pathlib import Path
    record_path = tmp_path/'connection.json'
    scope = Scope('batch-owner','batch-project','batch-host')
    process = subprocess.Popen([str(Path('target/debug/hypermid-daemon').resolve()), '--socket',str(tmp_path/'native.sock'),'--connection-record',str(record_path),
        '--local-credential-id','batch-credential','--local-owner-id',str(scope.owner_id),'--local-project-id',str(scope.project_id),'--local-workspace-id',str(scope.workspace_id),
        '--local-capability-id','batch-capability','--local-capability-operation','administer','--local-capability-operation','read','--local-capability-resource','memory-service','--local-capability-resource','memory-records','--local-capability-expires-ms',str(int(time.time()*1000)+120000)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,env=os.environ.copy())
    try:
        for _ in range(500):
            if record_path.exists(): break
            assert process.poll() is None
            await asyncio.sleep(.02)
        assert record_path.exists()
        provider = HypermidMemoryProvider(record_path, scope=scope, capability_id=Id('batch-capability'))
        async with configured_completion(lambda request,n: 'ACTUAL-SDK-BATCH-DONE') as (requests, _):
            save_active_models({'chat':['ContractSDK:good'],'background':['ContractSDK:good'],'reasoning':['ContractSDK:good']})
            def factory(session_key, **options):
                return resolve_provider_for_use_case('chat',session_key=session_key,model_override=options.get('model_override'),cwd=options.get('cwd'))
            await _run_batch(tmp_path, provider, factory, requests)
        await asyncio.to_thread(provider.close)
    finally:
        process.terminate()
        process.wait(timeout=5)


async def _run_batch(tmp_path, provider, factory, requests):
    from gideon.cognition.context import PromptAssembler
    from gideon.security.execution_lineage import host_runtime_admission
    from gideon.security.session_credentials import bind_execution
    from gideon.extensions.providers.provider_bridge import resolve_provider_for_use_case
    from gideon.engine.delegation_host import DelegationHost
    import logging
    from pathlib import Path
    sessions = ConversationDirectory(AppConfig(), provider_factory=factory)
    assembler = PromptAssembler()
    launched = []
    async def spawn_ask(*args):
        raise AssertionError('batch leaf must consume the batch Allow, not ask again')
    manager = DelegationSupervisor(sessions, assembler, on_spawn_approval=spawn_ask,
        validate_batch_start_approval=batch_start.validate_start_approval)
    # Exercise actual manager's memory/incident/budget admission; only model execution is substituted.
    log = ConversationLog(tmp_path/'sessions')
    log.append('batchchat', 'user', 'Please run these two tasks')
    log.update_metadata('batchchat', {'initiator': principal_record(YOU), 'memory_mode':'incognito', 'lifecycle':'active'})
    state = ConsoleState(sessions, time.time(), subagents=manager, conversation_log=log)
    supervisor = WorkflowWatchdog(services=EngineServices(subagents=manager, memory=provider))
    state.workflows = supervisor
    proof = session_credentials.begin_turn('dashboard:batchchat', YOU, turn_id='batch-turn', origin_session_key='batchchat', memory_mode='incognito')
    with host_runtime_admission(proof):
        host_runtime = resolve_provider_for_use_case('chat',session_key=proof.work.session_key)
        await host_runtime.start()
    bind_execution(proof, host_runtime)
    events=[]
    host = DelegationHost(SimpleNamespace(dashboard_state=state), SimpleNamespace(logger=logging.getLogger('batch-test')))
    manager._on_event = host.event
    state.broadcast_ws = lambda kind,payload,**kwargs: events.append((kind,payload))
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
            session_credentials.end_turn(proof)
            proof = None
            supervisor._private_receipts.clear()
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
            await controller.wait_for_terminal(timeout=25)
            launched = list(manager._agents.values())
            assert len(launched) == 2
            assert all(info.done and not info.error and info.result == 'ACTUAL-SDK-BATCH-DONE' for info in launched), [(info.error, info.result) for info in launched]
            assert all(info.batch_start_approval and info.approval_mode == 'ask' for info in launched)
            assert all(Path(info.result_path).read_text() == 'ACTUAL-SDK-BATCH-DONE' for info in launched)
            assert len(requests) == 2 and all(request['model'] == 'good' for request in requests)
            assert any(kind == 'subagent_spawn' and payload['session'] == 'batchchat' and payload.get('node_id') == 'first' for kind,payload in events)
            assert len([1 for kind,payload in events if kind == 'subagent_done' and payload['session'] == 'batchchat']) == 2
            assert store.read_spec(row['run_id'])['root']['children'][0]['config']['prompt'] == spec['root']['children'][0]['config']['prompt']
            assert len(state._pending_approvals) == 0
            second = await client.post('/api/approvals/'+body['approval']+'/approve', json={'expected_revision':entry['revision']}, headers=owner)
            assert second.status == 404
    finally:
        session_credentials.end_turn(proof)
        for task in list(getattr(state,'_workflow_batch_tasks',{}).values()): task.cancel()
        await supervisor.stop()
        await sessions.close_all()
        await host_runtime.shutdown()
