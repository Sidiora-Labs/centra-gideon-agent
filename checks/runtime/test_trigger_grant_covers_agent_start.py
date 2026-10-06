from __future__ import annotations

import asyncio
from types import SimpleNamespace

from gideon.automation.triggers.grants import (
    action_revision,
    agent_start_approval,
    grant,
)
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import AppConfig
from gideon.engine.delegation_host import DelegationHost
from gideon.engine.hooks import HookManager, HooksConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.subagent import DelegationSupervisor, SubagentInfo, _ExecutionPass
from gideon.integrations.action_providers import ActionContext
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
from gideon.security.approval_answer import YOU


def test_trigger_allow_covers_only_unchanged_agent_start(tmp_path, monkeypatch):
    home = tmp_path / "gideon"
    home.mkdir()
    (home / "config.json").write_text(
        '{"agent":{"approval_mode":"interactive"}}', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    action = {"task_template": "Review $CONTEXT", "approval_mode": "auto"}
    store = TriggerStore()
    trigger = Trigger(
        id="clock:trigger-allow",
        name="Trigger Allow",
        kind="clock",
        enabled=True,
        spec={"kind": "interval", "interval_secs": 60},
        workflow={"inline": {"provider": "invoke-agent", "config": action}},
    )
    store.upsert(trigger)
    saved = store.get(trigger.id).trigger
    revision = action_revision(saved)
    assert revision and grant(saved, confirmed_revision=revision, principal=YOU)
    store.upsert(saved)

    sessions = ConversationDirectory(AppConfig.load())
    approvals = []
    launched = []

    async def request_start_approval(*args):
        approvals.append(args)
        return False

    manager = DelegationSupervisor(
        sessions=sessions,
        ctx_builder=SimpleNamespace(hooks=HookManager(HooksConfig())),
        on_spawn_approval=request_start_approval,
        validate_trigger_start_approval=DelegationHost.validate_trigger_start_approval,
    )

    async def observe_start(info):
        launched.append(info)

    manager._run = observe_start

    def spawn_background(coroutine):
        return asyncio.create_task(coroutine)

    import gideon.integrations.action_providers.invoke_agent_provider as provider_module

    monkeypatch.setattr(
        provider_module,
        "get_action_services",
        lambda: ActionServices(None, spawn_background, manager),
    )
    provider = provider_module.InvokeAgentActionProvider()
    context = ActionContext(event="clock.fired", payload={"trigger_id": trigger.id})

    async def scenario():
        result = await provider.execute(action, context)
        assert result.success
        await asyncio.sleep(0)
        await asyncio.gather(*manager._tasks.values())

    asyncio.run(scenario())

    assert len(launched) == 1
    assert approvals == []
    info = launched[0]
    assert info.approval_mode == ""
    assert manager._execution_policy(info) == ""
    assert "unattended" not in manager._runner_arguments(info, "")
    duplicate = SubagentInfo(
        id="replayed-start",
        task="Review $CONTEXT",
        trigger_start_approval=info.trigger_start_approval,
    )
    manager._dispatch_run(duplicate)
    assert duplicate.done and "Allow" in duplicate.error
    assert len(launched) == 1

    async def tool_approval(event, parent_session_key):
        approvals.append((event.title, parent_session_key))
        return True

    manager._on_tool_approval = tool_approval
    run = _ExecutionPass(
        info=info,
        client=None,
        session_key="",
        policy="",
        turn_limit=1,
        research=False,
    )
    tool_event = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST, title="read_file", request_id="tool-1"
    )
    assert asyncio.run(manager._permission_decision(run, tool_event))[0]
    assert approvals == [("read_file", "")]

    old_approval = agent_start_approval(trigger.id, action)
    assert old_approval is not None
    current = store.get(trigger.id).trigger
    current.workflow["inline"]["config"] = {
        "task_template": "Changed $CONTEXT",
        "approval_mode": "auto",
    }
    store.upsert(current)

    async def changed_action_is_refused():
        result = await provider.execute(action, context)
        assert result.success is False

    asyncio.run(changed_action_is_refused())
    stale = SubagentInfo(
        id="stale-start",
        task="Review $CONTEXT",
        trigger_start_approval=old_approval,
    )
    manager._dispatch_run(stale)
    assert stale.done
    assert "Allow" in stale.error
    assert len(launched) == 1

    restored = store.get(trigger.id).trigger
    restored.workflow["inline"]["config"] = dict(action)
    store.upsert(restored)
    asyncio.run(changed_action_is_refused())

    store.delete(trigger.id)
    asyncio.run(changed_action_is_refused())
