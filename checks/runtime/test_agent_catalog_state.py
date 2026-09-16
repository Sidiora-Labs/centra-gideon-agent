import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AgentProfile, AgentsRoutingConfig, AppConfig
from gideon.engine.agents import defaults, registry, routing
from gideon.engine.agents.identity import resolve_agent_id
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentProvider, AgentRuntimeDefinition
from gideon.integrations.llm.acp_agent import AcpAgentProvider
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.fixture
def catalog_home(tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    return home


@pytest.mark.parametrize(
    "builder,prompt,skills,tools",
    [
        (
            defaults.make_default_native_profile,
            defaults.DEFAULT_NATIVE_SYSTEM_PROMPT,
            [],
            [],
        ),
        (
            defaults.make_loop_worker_profile,
            defaults.LOOP_WORKER_SYSTEM_PROMPT,
            ["loop-worker"],
            [],
        ),
        (
            defaults.make_loop_planner_profile,
            defaults.LOOP_PLANNER_SYSTEM_PROMPT,
            [],
            [],
        ),
        (defaults.make_coder_profile, defaults.CODER_SYSTEM_PROMPT, [], []),
        (
            defaults.make_code_planner_profile,
            defaults.CODE_PLANNER_SYSTEM_PROMPT,
            [],
            [],
        ),
        (defaults.make_lite_agent_profile, defaults.LITE_AGENT_SYSTEM_PROMPT, [], []),
        (
            defaults.make_template_refiner_profile,
            defaults.TEMPLATE_REFINER_SYSTEM_PROMPT,
            [],
            ["refiner_evidence", "propose_template_diff"],
        ),
    ],
)
def test_builtin_profiles_construct_real_independent_configuration(
    builder, prompt, skills, tools
):
    profile = builder(AgentProfile)
    assert (profile.provider, profile.source, profile.model) == (
        "native",
        "builtin",
        "",
    )
    assert profile.system_prompt == prompt
    assert profile.skills == skills and profile.tools == tools
    profile.skills.append("local-change")
    profile.tools.append("local-tool")
    again = builder(AgentProfile)
    assert again.skills == skills and again.tools == tools


def test_registry_exact_override_and_replacement_keep_registration_order():
    before = dict(registry._providers)
    try:
        registry.register_agent_provider("catalog-owned", NativeAgentRuntime)
        registry.register_agent_provider("catalog-owned:special", AcpAgentProvider)
        assert (
            registry.get_agent_provider_class("catalog-owned:other")
            is NativeAgentRuntime
        )
        assert (
            registry.get_agent_provider_class("catalog-owned:special")
            is AcpAgentProvider
        )
        registry.register_agent_provider("catalog-owned", AcpAgentProvider)
        assert (
            registry.get_agent_provider_class("catalog-owned:other") is AcpAgentProvider
        )
        assert registry.list_agent_providers()[-2:] == [
            "catalog-owned",
            "catalog-owned:special",
        ]
        registry._providers.pop("catalog-owned:special")
        assert (
            registry.get_agent_provider_class("catalog-owned:special")
            is AcpAgentProvider
        )
    finally:
        registry._providers.clear()
        registry._providers.update(before)


@pytest.mark.asyncio
async def test_base_command_forwarding_drives_a_real_bounded_native_turn(catalog_home):
    model = AcpAgentProvider(command=["__unused_for_zero_turn_budget__"])
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition("owned-relay"),
        model_provider=model,
        cwd=catalog_home,
        session_key="catalog-owned",
        max_turns=0,
    )
    events = [event async for event in runtime.stream_command("/catalog-owned")]
    assert runtime.supports_native_commands is False
    assert runtime._messages == [{"role": "user", "content": "/catalog-owned"}]
    assert len(events) == 1
    assert events[0].stop_reason == "max_turns"
    assert model.pid is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_base_discovery_and_readiness_return_independent_records():
    first = await AgentProvider.probe_readiness({})
    first.state = "modified"
    second = await AgentProvider.probe_readiness({})
    assert second.ready is True and second.state == "ready"
    discovered = await AgentProvider.discover_agents({})
    discovered.append("local change")
    assert await AgentProvider.discover_agents({}) == []
    assert (
        AgentProvider.agents_from_snapshot({}, {"modes": {"availableModes": []}}) == []
    )


@pytest.mark.parametrize(
    "agent,runtime,persona,expected",
    [
        ("ignored", " acp:cli:variant ", " mode ", "acp:cli:variant/mode"),
        ("ignored", "acp:", "mode", "acp:/mode"),
        (" native ", "ACP:cli", "fallback", "native"),
        (" ", "native", "fallback", ""),
        (None, "unknown", " fallback ", "fallback"),
    ],
)
def test_identity_keeps_runtime_and_persona_boundary(agent, runtime, persona, expected):
    assert resolve_agent_id(agent, runtime, persona) == expected


def test_keyword_ties_preserve_first_phrase_guard(catalog_home):
    message = "inspect the database"
    short_first = [("dba", "Database", "the database, inspect the database")]
    long_first = [("dba", "Database", "inspect the database, the database")]
    assert routing.classify(message, short_first) is None
    result = routing.classify(message, long_first)
    assert result is not None and result.agent == "dba" and result.method == "keyword"
    assert (
        routing.classify(
            message, long_first + [("other", "Other", "inspect the database")]
        )
        is None
    )


def test_vector_cache_preserves_model_identity_without_an_embedder(catalog_home):
    vector = [1.0, 0.0]
    cache = {"dba": ("owned:model", vector)}
    assert (
        routing._candidate_vector(
            "dba", "database", "inspect schema", "owned:model", cache
        )
        is vector
    )
    assert (
        routing._candidate_vector(
            "dba", "database", "inspect schema", "other:model", cache
        )
        is None
    )
    assert cache == {"dba": ("owned:model", vector)}
    assert routing._cosine(vector, [0.0, 1.0]) == 0.0
    assert routing._cosine(vector, [2.0, 0.0]) == 1.0
    assert routing._cosine([0.0, 0.0], vector) == 0.0


def test_suppression_uses_real_persisted_cooldown_and_mute_state(catalog_home):
    assert routing.record_dismiss("DBA", now=10) == {
        "agent": "dba",
        "count": 1,
        "muted": False,
    }
    assert routing.is_suppressed("dba", now=20, cooldown_hours=1)
    assert not routing.is_suppressed("dba", now=3610, cooldown_hours=1)
    routing.record_dismiss("dBa", now=40)
    assert routing.record_dismiss("dba", now=50)["muted"] is True
    document = json.loads(
        (catalog_home / "entity_settings" / "agent_routing.json").read_text()
    )
    assert document["muted"] == ["dba"]
    assert document["dismissals"]["dba"] == {"count": 3, "last_dismissed_at": 50}
    assert routing.is_suppressed("DBA", now=100000, cooldown_hours=0)
    routing.unmute("DBA")
    assert routing.routing_status() == {"muted": [], "dismissals": {}}


def test_real_config_and_chat_apply_suggestion_frequency_without_rebinding(
    catalog_home,
):
    config = AppConfig()
    config.agents[defaults.DEFAULT_NATIVE_AGENT_NAME] = (
        defaults.make_default_native_profile(AgentProfile)
    )
    config.default_agent = defaults.DEFAULT_NATIVE_AGENT_NAME
    config.agents["catalog-specialist"] = AgentProfile(
        specialty="Database tuning", route_hints="optimize slow database query"
    )
    config.agents_routing = AgentsRoutingConfig(
        enabled=True, min_confidence=0.62, cooldown_hours=24
    )
    config.save()
    state = ConsoleState(sessions=None, start_time=0)
    session = _ChatSession("catalog-chat")
    session.messages.append({"role": "user", "content": "first"})
    message = "please optimize slow database query"
    result = routing.suggest_for_send(state, session, message)
    assert result is not None and result.agent == "catalog-specialist"
    assert session.agent == ""
    session.messages.append({"role": "user", "content": "second"})
    assert routing.suggest_for_send(state, session, message) is None
    session.messages.extend({"role": "user", "content": str(turn)} for turn in range(4))
    assert (
        routing.suggest_for_send(state, session, message).agent == "catalog-specialist"
    )
    assert session.agent == ""
    assert state._routing_last_turn[session.key] == 6


@pytest.mark.asyncio
async def test_native_discovery_endpoint_through_actual_http(catalog_home):
    from gideon.interfaces.dashboard.handlers.providers import api_agent_provider_agents

    app = web.Application()
    app.router.add_get("/api/agent-providers/{id}/agents", api_agent_provider_agents)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/agent-providers/native/agents")
        assert response.status == 200
        assert await response.json() == {
            "agents": [],
            "permission_modes": [],
            "cached": False,
        }
