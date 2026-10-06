"""Durable app scope through real run records, stage runtime and nested controller."""

import asyncio
import json
import uuid

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    folder = tmp_path / "apps" / "scope-app"
    folder.mkdir(parents=True)
    (folder / "installed.json").write_text(
        json.dumps({"name": "scope-app", "enabled": True, "version": "1.0.0"})
    )
    (folder / "app.json").write_text(
        json.dumps(
            {"name": "scope-app", "version": "1.0.0", "permissions": {"agent": "read"}}
        )
    )
    script = tmp_path / "model.json"
    script.write_text(json.dumps({"version": 1, "turns": [{"text": "READ-DONE"}]}))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    return tmp_path


def create_run(tier="read", *, mode="normal", extra=None):
    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import WorkflowRun
    from gideon.extensions.apps.app_work import AppWork, stamp

    return store.create(
        WorkflowRun(
            id="",
            workflow_name="scope-work",
            extra=stamp(
                {"memory_mode": mode, **(extra or {})}, AppWork("scope-app", tier)
            ),
        )
    )


def test_store_reload_malformed_and_live_tier(home):
    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import WorkflowRun
    from gideon.extensions.apps.app_work import from_record, of_run_id

    run = create_run()
    work = of_run_id(store.get(run.id).id)
    assert work.app == "scope-app" and work.current_tier() == "read"
    assert from_record({"app_work": "bad"}).current_tier() == ""
    assert of_run_id("missing-record").current_tier() == ""
    (home / "apps" / "scope-app" / "installed.json").write_text(
        json.dumps({"name": "scope-app", "enabled": False})
    )
    assert of_run_id(run.id).current_tier() == ""


def test_real_fork_preserves_scope_privacy_and_other_extra(home):
    from gideon.automation.workflows import checkpoints, store
    from gideon.extensions.apps.app_work import of_run_id

    parent = create_run(
        mode="incognito",
        extra={
            "custom": {"keep": [1]},
            "owner_workflow_versions": {"entries": {"scope-child": {"version": 3}}},
        },
    )
    result = checkpoints.fork_run(
        parent, {"root": {"kind": "transform", "config": {"expr": "done"}}}, {}
    )
    child = store.get(result.child.id)
    assert child.extra == parent.extra
    assert of_run_id(child.id).tier == "read"
    child.extra["custom"]["keep"].append(2)
    assert parent.extra["custom"]["keep"] == [1]


@pytest.mark.asyncio
async def test_real_native_stage_enforces_stored_scope(home):
    from gideon.automation.workflows import engine
    from gideon.automation.workflows.bindings import BindingContext
    from gideon.automation.workflows.models import InstanceState, Node, NodeKind
    from gideon.cognition.context import PromptAssembler
    from gideon.core.config.loader import AppConfig
    from gideon.engine.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        NativeBuiltinToolProvider,
    )
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.subagent import DelegationSupervisor
    from gideon.extensions.apps.app_work import release_session
    from gideon.integrations.llm.scripted import ScriptedProvider

    instances = []

    def factory(key, **options):
        result = NativeAgentRuntime(
            definition=AgentRuntimeDefinition("researcher"),
            model_provider=ScriptedProvider(),
            app_work=options.get("app_work"),
            tool_providers=[
                NativeBuiltinToolProvider(
                    cwd=home,
                    categories=PLATFORM_CATEGORIES,
                    provider_name="gideon-filesystem",
                )
            ],
            session_key=key,
            cwd=home,
        )
        instances.append(result)
        return result

    sessions = ConversationDirectory(AppConfig.load(), provider_factory=factory)
    manager = DelegationSupervisor(sessions, PromptAssembler())
    run = create_run()
    try:
        forbidden = await engine.dispatch_stage(
            Node(
                NodeKind.STAGE,
                id="change",
                config={"prompt": "write", "capability": "mutating"},
            ),
            BindingContext(),
            subagents=manager,
            run_id=run.id,
        )
        assert forbidden.state == InstanceState.FAILED
        assert not instances
        allowed = await engine.dispatch_stage(
            Node(
                NodeKind.STAGE,
                id="read",
                config={"prompt": "review", "capability": "research"},
            ),
            BindingContext(),
            subagents=manager,
            run_id=run.id,
        )
        assert allowed.state == InstanceState.RUNNING
        info = manager.get(allowed.output["subagent_id"])
        for _ in range(200):
            if info.done:
                break
            await asyncio.sleep(0.01)
        assert info.done and not info.error
        assert info.app_work.app == "scope-app" and info.app_work.tier == "read"
        assert "write_file" not in instances[0]._tool_index
        assert info.result == "READ-DONE"
    finally:
        await sessions.close_all()
        for info in manager.all_agents:
            release_session(f"subagent:{info.id}")


@pytest.mark.asyncio
async def test_nested_real_controller_preserves_scope_and_privacy(home):
    from gideon.automation.workflows import defs, engine, native_defs, store
    from gideon.automation.workflows.bindings import BindingContext
    from gideon.automation.workflows.models import InstanceState, Node, NodeKind
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.extensions.apps.app_work import of_run_id

    provider = native_defs.NativeWorkflowDefProvider()
    previous = defs.get_provider("native")
    defs.register_provider(provider)
    supervisor = WorkflowWatchdog()
    parent = create_run(
        mode="incognito",
        extra={"work_principal": {"kind": "app", "name": "scope-app", "tenant": ""}},
    )
    try:
        await provider.save_def(
            name="scope-child",
            root={"kind": "transform", "id": "result", "config": {"expr": "done"}},
        )
        result = await engine.dispatch_subworkflow(
            Node(NodeKind.SUBWORKFLOW, id="nested", config={"ref": "scope-child"}),
            BindingContext(),
            run_id=parent.id,
            supervisor=supervisor,
            timeout=3,
        )
        assert result.state == InstanceState.DONE
        child = store.get(result.output["child_run_id"])
        assert child.parent_run_id == parent.id
        assert child.extra["memory_mode"] == "incognito"
        assert child.extra["work_principal"] == parent.extra["work_principal"]
        assert of_run_id(child.id).current_tier() == "read"
    finally:
        await supervisor.stop()
        defs.unregister_provider("native")
        if previous is not None:
            defs.register_provider(previous)


@pytest.mark.asyncio
async def test_app_action_agent_escape_refused_before_provider(home):
    from gideon.automation.workflows import engine
    from gideon.automation.workflows.bindings import BindingContext
    from gideon.automation.workflows.models import InstanceState, Node, NodeKind

    run = create_run()
    for name in (
        "invoke-agent",
        "run-prompt",
        "run-workflow",
        "bash",
        "create-task",
        "call-app-route",
    ):
        result = await engine.dispatch_action(
            Node(NodeKind.ACTION, id=name, config={"provider": name}),
            BindingContext(),
            run_id=run.id,
        )
        assert result.state == InstanceState.FAILED
        assert result.failure.failure_class.value == "permission"


@pytest.mark.asyncio
async def test_service_scope_creation_and_draft_cannot_acquire_owner_run(home):
    from gideon.automation.workflows import defs, native_defs, service, store
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.extensions.apps.app_work import AppWork, of_run_id

    provider = native_defs.NativeWorkflowDefProvider()
    previous = defs.get_provider("native")
    defs.register_provider(provider)
    supervisor = WorkflowWatchdog()
    try:
        await provider.save_def(
            name="scope-top",
            root={"kind": "transform", "id": "result", "config": {"expr": "done"}},
        )
        started = await service.start_run(
            name="scope-top",
            app_work=AppWork.for_app("scope-app"),
            work_memory_mode="persistent",
            supervisor=supervisor,
        )
        assert started["ok"]
        assert of_run_id(started["run_id"]).current_tier() == "read"
        from gideon.automation.workflows.models import WorkflowRun

        owner = store.create(WorkflowRun(id="", workflow_name="scope-top"))
        refused = await service.start_draft(
            owner.id,
            app_work=AppWork.for_app("scope-app"),
            work_memory_mode="persistent",
            supervisor=supervisor,
        )
        assert not refused["ok"] and "APP_RUN_SCOPE" in str(refused)
    finally:
        await supervisor.stop()
        defs.unregister_provider("native")
        if previous is not None:
            defs.register_provider(previous)
