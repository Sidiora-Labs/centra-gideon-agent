"""Actual authenticated trigger review transfers host provenance into native runs."""
import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers import grants
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.triggers.review import TriggerReviewStore
from test_trigger_review_completion import observations
from gideon.automation.workflows import defs, store as runs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.interfaces.dashboard.handlers.triggers import register_trigger_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_persistent_secret, reset_secret_cache
from gideon.integrations.action_providers.services import ActionServices, get_action_services, set_action_services
from gideon.security.approval_answer import YOU
from gideon.security.durable_work import (RUN_ORIGIN_KEY, recorded_run_origin, verified_run_origin,
    restore_accepted_origin, persist_accepted_origin, accepted_origin_values)
from test_trigger_completion_lifecycle import one_shot
from test_background_completion_contract import configured_completion
from gideon.extensions.providers.use_cases import save_active_models


@pytest.mark.asyncio
async def test_actual_owner_review_provider_preserves_signature_and_terminal_audit(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    use_persistent_secret()
    reset_secret_cache()
    now = time.time()
    provider = NativeWorkflowDefProvider()
    previous = defs.get_provider('native')
    defs.register_provider(provider)
    previous_services = get_action_services()
    watchdog = WorkflowWatchdog()
    state = ConsoleState(None, now)
    set_action_services(ActionServices(state, asyncio.create_task, workflows=watchdog))
    trigger = one_shot('clock:authenticated-review', now)
    trigger.enabled = False
    trigger.next_fire_at = ''
    trigger.workflow = {'provider': 'run-workflow', 'config': {'workflow': 'authenticated-review'}}
    try:
        async with configured_completion(lambda request, n: 'REVIEWED-WORK-DONE') as (requests, _):
            save_active_models({'background': ['ContractSDK:good'], 'chat': ['ContractSDK:good'], 'reasoning': ['ContractSDK:good']})
            await provider.save_def(name='authenticated-review', root={'kind': 'infer', 'id': 'infer', 'config': {'prompt': 'complete reviewed work'}}, provenance='user', _owner_saved=True)
            question = grants.question(trigger)
            assert grants.grant(trigger, confirmed_revision=question.revision, principal=YOU, shown=question.shown)
            store = TriggerStore(base_dir=tmp_path)
            store.upsert(trigger)
            card = TriggerReviewStore(tmp_path).add_boot_observations(store, observations(trigger, now), [], now=now)[0]
            server = web.Application(middlewares=[token_auth_middleware(port=0)])
            server['state'] = state
            register_trigger_routes(server)
            async with TestClient(TestServer(server)) as client:
                response = await client.post('/api/triggers/review', headers={'Authorization': 'Bearer ' + generate_token('sir')}, json={
                    'trigger_id': card['trigger_id'], 'review_id': card['id'], 'decision': 'run_now',
                    'expected_revision': card['action_revision']})
                body = await response.json()
                assert response.status == 202 and body['outcome'] == 'pending', body
                await asyncio.wait_for(asyncio.gather(*tuple(state._background_tasks)), timeout=15)
            actual = runs.list_runs(workflow_name='authenticated-review')[0][0]
            origin = recorded_run_origin(actual)
            assert origin['initiator'] == {'kind': 'owner', 'name': 'sir', 'tenant': ''}
            assert origin['run_id'] == actual.id and actual.origin.trigger_id == trigger.id
            assert verified_run_origin(actual) is None
            assert requests and not requests[0].get('tools')
            record = actual.extra[RUN_ORIGIN_KEY]
            restored = restore_accepted_origin(record)
            assert persist_accepted_origin(restored) == record
            assert accepted_origin_values(restored)['run_id'] == actual.id
            forged = {**record, 'signature': 'forged'}
            assert restore_accepted_origin(forged) is None
    finally:
        await watchdog.stop()
        set_action_services(previous_services)
        if previous is None:
            defs.unregister_provider('native')
        else:
            defs.register_provider(previous)
        reset_secret_cache()
