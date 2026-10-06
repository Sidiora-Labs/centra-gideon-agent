"""Real installed app scope and native/subagent tier boundaries."""

import json
import uuid
from pathlib import Path

import pytest

from gideon.extensions.apps.app_work import (
    AppWork,
    bind_session,
    for_job,
    intersect,
    of_session,
    release_session,
)


def install(home, tier, *, enabled=True):
    folder = home / "apps" / "tier-app"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "installed.json").write_text(
        json.dumps({"name": "tier-app", "enabled": enabled, "version": "1.0.0"})
    )
    (folder / "app.json").write_text(
        json.dumps(
            {
                "name": "tier-app",
                "version": "1.0.0",
                "permissions": {"agent": tier, "cron": True},
                "crons": [{"name": "daily", "message": "analyse", "every": 60}],
            }
        )
    )
    return folder


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"version": 1, "turns": [{"text": "analysed"}]}))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    return tmp_path


def runtime(home, work):
    from gideon.engine.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        NativeBuiltinToolProvider,
    )
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.scripted import ScriptedProvider

    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition("researcher"),
        app_work=work,
        model_provider=ScriptedProvider(),
        tool_providers=[
            NativeBuiltinToolProvider(
                cwd=home,
                categories=PLATFORM_CATEGORIES,
                provider_name="gideon-filesystem",
            )
        ],
        cwd=home,
    )


def test_live_manifest_disabled_and_child_intersection(home):
    install(home, "read")
    work = AppWork.for_app("tier-app")
    assert work.tier == "read"
    assert work.child("tools").tier == "read"
    assert work.child("text").tier == "text"
    assert intersect("invalid", "tools") == ""
    install(home, "tools")
    assert work.current_tier() == "read"
    install(home, "tools", enabled=False)
    assert work.current_tier() == ""
    assert work.refusal("read_file")


def test_host_bind_never_widens_or_changes_app(home):
    install(home, "tools")
    key = uuid.uuid4().hex
    try:
        bind_session(key, AppWork("tier-app", "read"))
        bind_session(key, AppWork("tier-app", "tools"))
        assert of_session(key).tier == "read"
        bind_session(key, AppWork("other", "tools"))
        assert of_session(key).tier == ""
        assert of_session("app:tier-app") is None
    finally:
        release_session(key)


def test_job_identity_requires_actual_declared_cron(home):
    install(home, "read")
    assert for_job("app:tier-app:daily").tier == "read"
    assert for_job("app:tier-app:invented").tier == ""
    assert for_job("ordinary-job") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["text", "read", "tools"])
async def test_native_catalog_invocation_and_owner_auto_scope(home, tier):
    install(home, tier)
    agent = runtime(home, AppWork.for_app("tier-app"))
    await agent.start()
    agent.set_approval_policy("auto")
    try:
        names = {item.name for item in agent._tool_defs}
        if tier == "text":
            assert names == set() and agent._tool_schema == []
        else:
            assert "read_file" in names
            assert ("write_file" in names) == (tier == "tools")
        if tier == "tools":
            assert agent._requires_approval("write_file")
        for method in ("_invoke", "_guard_and_invoke"):
            if tier == "tools":
                break
            meta = {}
            arguments = {"path": "never-written", "content": "blocked"}
            result = (
                await agent._invoke("write_file", arguments, meta_sink=meta)
                if method == "_invoke"
                else await agent._guard_and_invoke(
                    None, "write_file", arguments, meta=meta
                )
            )
            assert meta == {"ok": False, "refused_by": "app_agent_tier"}
            assert "app" in result or "read-only" in result
        assert not (home / "never-written").exists()
        install(home, tier, enabled=False)
        meta = {}
        assert "no agent work" in await agent._invoke(
            "read_file", {"path": "x"}, meta_sink=meta
        )
    finally:
        await agent.shutdown()


def test_acp_cannot_claim_app_tier(home):
    from gideon.extensions.providers.provider_bridge import (
        ProviderResolutionError,
        resolve_provider_for_use_case,
    )

    install(home, "read")
    with pytest.raises(ProviderResolutionError, match="native runtime"):
        resolve_provider_for_use_case(
            "chat", provider_kind="acp:unconfined", app_work=AppWork.for_app("tier-app")
        )


@pytest.mark.asyncio
async def test_actual_text_subagent_never_assembles_memory(home):
    from gideon.cognition.context import PromptAssembler
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.subagent import DelegationSupervisor, SubagentInfo

    install(home, "text")
    work = AppWork.for_app("tier-app")
    instances = []

    def factory(key, **options):
        agent = runtime(home, options.get("app_work"))
        instances.append(agent)
        return agent

    sessions = ConversationDirectory(AppConfig.load(), provider_factory=factory)
    supervisor = DelegationSupervisor(sessions, PromptAssembler())
    info = SubagentInfo(
        id=uuid.uuid4().hex[:8],
        task="TASK-ALONE",
        app_work=work,
        capability_class="text",
    )
    try:
        assert supervisor._spawn_permission(info) == "app_permission"
        assert supervisor._execution_policy(info) == "ask"
        await supervisor._run_inner(info, f"subagent:{info.id}")
        messages = instances[0]._messages
        assert [m["content"] for m in messages if m["role"] == "user"] == ["TASK-ALONE"]
        assert instances[0]._model.last_complete_call["tool_count"] == 0
    finally:
        await sessions.close_all()
        release_session(f"subagent:{info.id}")


@pytest.mark.asyncio
async def test_real_tool_incapable_model_never_claims_app_work_completed(home):
    install(home, "read")
    agent = runtime(home, AppWork.for_app("tier-app"))
    agent._model.supports_tools = False
    with pytest.raises(RuntimeError, match="task did not run"):
        await agent.start()
    assert agent._model.turn_index == 0
    await agent.shutdown()
