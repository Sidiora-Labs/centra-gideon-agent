"""Authenticated canonical run receipts through real helper and native executions."""
import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.workflows import store, engine
from gideon.automation.workflows.bindings import BindingContext
from gideon.automation.workflows.models import WorkflowRun, RunStatus, Node, NodeKind, InstanceState
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.handlers import register_workflow_routes
from gideon.cognition.context import PromptAssembler
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.subagent import DelegationSupervisor
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_persistent_secret, reset_secret_cache
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.credentials import Credential
from gideon.extensions.providers.use_cases import save_active_models
from gideon.security.durable_work import RUN_ORIGIN_KEY, verified_run_origin, workflow_work
from gideon.security.session_credentials import current_work, credential_for, verify
from test_background_completion_contract import configured_completion


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    use_persistent_secret()
    reset_secret_cache()
    yield tmp_path
    reset_secret_cache()


def app(watchdog):
    state = ConsoleState(sessions=None, start_time=0)
    state.workflows = watchdog
    server = web.Application(middlewares=[token_auth_middleware(port=0)])
    server['state'] = state
    register_workflow_routes(server)
    return server


async def start(client, root):
    response = await client.post('/api/workflows/runs', headers={'Authorization': 'Bearer ' + generate_token('sir')},
        json={'name': 'owner-proof', 'run_once': {'name': 'owner-proof', 'root': root}, 'skip_preflight': True})
    data = await response.json()
    assert response.status == 202, data
    return data['run_id']


@pytest.mark.asyncio
async def test_actual_owner_http_run_helper_execution_and_id_bound_receipt(home):
    watchdog = WorkflowWatchdog()
    async with configured_completion(lambda request, n: 'OWNER-HELPER-DONE') as (requests, _):
        save_active_models({'background': ['ContractSDK:good'], 'chat': ['ContractSDK:good'], 'reasoning': ['ContractSDK:good']})
        try:
            async with TestClient(TestServer(app(watchdog))) as client:
                run_id = await start(client, {'kind': 'infer', 'id': 'infer', 'config': {'prompt': 'execute owner request'}})
                assert await watchdog.controller(run_id).run_to_completion(timeout=10) == RunStatus.COMPLETE
            run = store.get(run_id)
            origin = json.loads(run.extra[RUN_ORIGIN_KEY]['payload'])
            assert origin['run_id'] == run.id and origin['initiator'] == {'kind': 'owner', 'name': 'sir', 'tenant': ''}
            assert origin['memory_mode'] == 'persistent'
            assert len(requests) == 1 and not requests[0].get('tools')
            assert current_work() is None
        finally:
            await watchdog.stop()


@pytest.mark.asyncio
async def test_actual_native_stage_preserves_original_owner_and_live_run_scope(home):
    instances = []
    async with configured_completion(lambda request, n: 'OWNER-NATIVE-DONE') as (requests, endpoint):
        def factory(key, **options):
            runtime = NativeAgentRuntime(definition=AgentRuntimeDefinition('researcher'),
                model_provider=OpenAIProvider(model='good', base_url=endpoint,
                    credential=Credential('local', 'api_key', 'local-transport-only')),
                session_key=key, cwd=home)
            instances.append(runtime)
            return runtime
        sessions = ConversationDirectory(AppConfig.load(), provider_factory=factory)
        manager = DelegationSupervisor(sessions, PromptAssembler())
        watchdog = WorkflowWatchdog(services=EngineServices(subagents=manager))
        try:
            async with TestClient(TestServer(app(watchdog))) as client:
                run_id = await start(client, {'kind': 'stage', 'id': 'native', 'config': {
                    'prompt': 'complete the reviewed owner task', 'approval_mode': 'auto', 'capability': 'research'}})
                assert await watchdog.controller(run_id).run_to_completion(timeout=15) == RunStatus.COMPLETE
            (info,) = manager.all_agents
            assert info.result == 'OWNER-NATIVE-DONE' and not info.error
            assert info.work_scope.durable_run_id == run_id
            assert info.work_scope.initiator.kind == 'owner' and info.work_scope.work_actor.kind == 'run'
            assert info.work_scope.memory_mode == 'persistent'
            assert len(instances) == 1 and len(requests) == 1
        finally:
            await watchdog.stop()
            await sessions.close_all()


@pytest.mark.asyncio
async def test_legacy_owner_reviewed_draft_and_operation_time_revocation(home):
    legacy = store.create(WorkflowRun(id='', workflow_name='legacy-owner', status=RunStatus.DRAFT,
                                     extra={'memory_mode': 'normal', 'work_initiator': {'kind': 'owner', 'name': 'sir', 'tenant': ''}}))
    store.write_spec(legacy.id, {'name': 'legacy-owner', 'root': {'kind': 'wait', 'id': 'hold', 'config': {'duration_secs': 300}}})
    denied = await engine.dispatch_action(Node(NodeKind.ACTION, id='claimed', config={'provider': 'not-executed'}), BindingContext(), run_id=legacy.id)
    assert denied.state == InstanceState.FAILED and 'review' in denied.failure.cause_plain
    watchdog = WorkflowWatchdog()
    try:
        async with TestClient(TestServer(app(watchdog))) as client:
            response = await client.post(f'/api/workflows/runs/{legacy.id}/start', headers={'Authorization': 'Bearer ' + generate_token('sir')}, json={})
            assert response.status == 202, await response.text()
        reviewed = store.get(legacy.id)
        assert verified_run_origin(reviewed) is not None
        with workflow_work(legacy.id, 'hold'):
            work = current_work()
            proof = credential_for(work.session_key)
            assert verify(proof, work.session_key) is work
            reviewed.extra['memory_mode'] = 'temporary'
            store.save(reviewed)
            assert verify(proof, work.session_key) is None
        reviewed.extra['memory_mode'] = 'normal'
        reviewed.extra[RUN_ORIGIN_KEY]['signature'] = 'forged'
        store.save(reviewed)
        assert verified_run_origin(store.get(legacy.id)) is None
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_owner_reviewed_app_run_keeps_app_actor_and_live_memory_consent(home):
    from gideon.extensions.apps.app_work import AppWork, stamp
    from gideon.security.session_credentials import memory_reach
    from gideon.hypermid.foundation import Scope
    folder = home / 'apps' / 'review-app'
    folder.mkdir(parents=True)
    (folder / 'installed.json').write_text(json.dumps({'name': 'review-app', 'enabled': True, 'version': '1.0.0'}))
    manifest = {'name': 'review-app', 'version': '1.0.0', 'permissions': {'agent': 'read', 'memory': 'shared'}}
    (folder / 'app.json').write_text(json.dumps(manifest))
    legacy = store.create(WorkflowRun(id='', workflow_name='legacy-app', status=RunStatus.DRAFT,
        extra=stamp({'memory_mode': 'normal'}, AppWork.for_app('review-app'))))
    store.write_spec(legacy.id, {'name': 'legacy-app', 'root': {'kind': 'wait', 'id': 'hold', 'config': {'duration_secs': 300}}})
    watchdog = WorkflowWatchdog()
    try:
        async with TestClient(TestServer(app(watchdog))) as client:
            headers = {'Authorization': 'Bearer ' + generate_token('sir')}
            response = await client.post(f'/api/workflows/runs/{legacy.id}/start', headers=headers, json={})
            assert response.status == 202, await response.text()
            with workflow_work(legacy.id, 'hold'):
                work = current_work()
                assert work.initiator.kind == 'owner' and work.work_actor.kind == 'app'
                scope = Scope('owner', 'project', 'workspace')
                assert memory_reach(scope).read_allowed
                manifest['permissions']['memory'] = ''
                (folder / 'app.json').write_text(json.dumps(manifest))
                assert not memory_reach(scope).read_allowed
                proof = credential_for(work.session_key)
                response = await client.post(f'/api/workflows/runs/{legacy.id}/resume', headers=headers, json={})
                assert response.status == 200, await response.text()
                assert verify(proof, work.session_key) is None
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_global_internal_secret_without_source_proof_cannot_mint_origin(home):
    from gideon.security.durable_work import accepted_origin_of_request
    secret = 'gateway-only-test-secret'
    server = web.Application(middlewares=[token_auth_middleware(port=0, internal_secret=secret,
        internal_routes=frozenset({'GET /origin'}))])
    server['local_secret'] = secret
    async def origin(request):
        return web.json_response({'issued': accepted_origin_of_request(request) is not None})
    server.router.add_get('/origin', origin)
    async with TestClient(TestServer(server)) as client:
        response = await client.get('/origin', headers={'X-Internal-Secret': secret, 'X-Session-Key': 'dashboard:forged-owner'})
        assert response.status == 200
        assert (await response.json())['issued'] is False
