"""Canonical reviewed grants admit trigger work without owner impersonation."""

import asyncio
import logging
import time

import pytest
from test_trigger_completion_lifecycle import one_shot

from gideon.automation.triggers import grants
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import defs
from gideon.automation.workflows import store as runs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.trigger_dispatch import TriggerDispatch
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import TRIGGER, YOU
from gideon.security.durable_work import (
    accepted_origin_values,
    accepted_trigger_origin,
    recorded_run_origin,
    workflow_work,
)
from gideon.security.session_credentials import current_work, memory_reach


@pytest.mark.asyncio
async def test_real_grant_store_dispatch_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gideon.engine.trigger_dispatch.config_dir", lambda: tmp_path, raising=False
    )
    provider = NativeWorkflowDefProvider()
    previous = defs.get_provider("native")
    defs.register_provider(provider)
    watchdog = WorkflowWatchdog()
    runtime = RuntimeCoordinator(AppConfig())
    state = ConsoleState(None, time.time())
    previous_services = get_action_services()
    set_action_services(ActionServices(state, asyncio.create_task, workflows=watchdog))
    try:
        await provider.save_def(
            name="reviewed-trigger",
            root={"kind": "sequence", "children": []},
            provenance="user",
            _owner_saved=True,
        )
        trigger = one_shot("clock:signed-grant", time.time())
        trigger.workflow = {
            "provider": "run-workflow",
            "config": {"workflow": "reviewed-trigger"},
        }
        trigger.capabilities["tools"] = ["memory_recall"]
        question = grants.question(trigger)
        assert grants.grant(
            trigger,
            confirmed_revision=question.revision,
            principal=YOU,
            shown=question.shown,
        )
        store = TriggerStore(base_dir=tmp_path)
        store.upsert(trigger)
        origin = accepted_trigger_origin(trigger.id)
        values = accepted_origin_values(origin)
        assert values["initiator"]["kind"] == TRIGGER
        assert values["trigger_acceptance"]["consent_owner"]["kind"] == "owner"
        assert values["trigger_acceptance"]["declared_tools"] == ["memory_recall"]
        await TriggerDispatch(
            runtime, trigger, {}, "trigger.fired", logging.getLogger(__name__)
        ).run()
        assert runtime._handler_tasks
        await asyncio.wait_for(asyncio.gather(*tuple(runtime._handler_tasks)), 10)
        actual = runs.list_runs(workflow_name="reviewed-trigger")[0][0]
        assert actual.is_terminal
        assert recorded_run_origin(actual)["initiator"]["kind"] == TRIGGER
        # Terminal receipts remain attribution, never execution credentials.
        with pytest.raises(PermissionError):
            with workflow_work(actual.id, "node"):
                pass
        store.upsert(trigger)
        assert accepted_trigger_origin(trigger.id) is not None
        trigger.capabilities["tools"].append("memory_remember")
        store.upsert(trigger)
        assert accepted_trigger_origin(trigger.id) is None
        trigger.capabilities["tools"].pop()
        store.upsert(trigger)
        assert accepted_trigger_origin(trigger.id) is None
        question = grants.question(trigger)
        assert grants.grant(
            trigger,
            confirmed_revision=question.revision,
            principal=YOU,
            shown=question.shown,
        )
        store.upsert(trigger)
        assert accepted_trigger_origin(trigger.id) is not None
        grants.revoke(trigger.id)
        assert accepted_trigger_origin(trigger.id) is None
    finally:
        await watchdog.stop()
        set_action_services(previous_services)
        if previous is None:
            defs.unregister_provider("native")
        else:
            defs.register_provider(previous)
