from gideon.engine.agents.native.failover import NoModelAnswered
from gideon.engine.subagent import DelegationSupervisor, SubagentInfo, _ExecutionPass
from gideon.integrations.llm.base import ModelSubstitution
from gideon.integrations.llm.events import AgentEvent, EVENT_COMPLETE
from gideon.operations import usage_ledger


def test_model_substitution_carries_a_structured_and_readable_record():
    substitution = ModelSubstitution(
        requested="local:preferred:model",
        served="cloud:backup:model",
        why="the preferred provider timed out",
        fix="Review the model order in Settings → Models.",
        who="this chat",
    )

    assert substitution.to_dict()["requested"] == "local:preferred:model"
    assert substitution.to_dict()["served"] == "cloud:backup:model"
    assert substitution.notice().startswith("Ran on cloud:backup:model instead of this chat")


def test_terminal_event_attributes_usage_to_the_actual_responder(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    event = AgentEvent(
        kind=EVENT_COMPLETE,
        input_tokens=120,
        output_tokens=30,
        served_model_ref="local:models/qwen:32b",
    )

    usage_ledger.record_from_event(
        event,
        source="subagent",
        provider="acp",
        model="requested-model",
        estimate_if_missing=False,
    )

    row = usage_ledger._iter_rows()[0]
    assert row["provider"] == "local"
    assert row["model"] == "models/qwen:32b"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 30


def test_all_failed_copy_keeps_each_named_model_and_repair_path():
    failure = NoModelAnswered(
        [
            ("local:preferred", "the provider timed out"),
            ("cloud:backup", "the provider is unavailable"),
        ]
    )

    text = failure.sentence(room_member="Analyst")
    assert "local:preferred" in text
    assert "cloud:backup" in text
    assert "Settings → Models" not in text
    assert "Agents page" in text


def test_subagent_completion_prices_and_records_the_terminal_responder(tmp_path, monkeypatch):
    import asyncio

    from gideon.operations.pricing import estimate_cost

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    info = SubagentInfo(
        id="responder-check",
        task="Compare the options.",
        model="preferred-model",
        parent_session_key="dashboard:parent",
        agent="Analyst",
    )
    run = _ExecutionPass(
        info=info,
        client=None,
        session_key="subagent:responder-check",
        policy="ask",
        turn_limit=5,
        research=True,
    )
    event = AgentEvent(
        kind=EVENT_COMPLETE,
        input_tokens=1_000_000,
        output_tokens=0,
        served_model_ref="anthropic:claude-sonnet-4.5",
    )

    asyncio.run(DelegationSupervisor._consume_completion(object.__new__(DelegationSupervisor), run, event))

    assert info.model == "preferred-model"
    assert info.served_model_ref == "anthropic:claude-sonnet-4.5"
    assert info.cost_usd == estimate_cost("claude-sonnet-4.5", input_tokens=1_000_000)
    row = usage_ledger._iter_rows()[0]
    assert row["source"] == "subagent"
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-sonnet-4.5"


def test_subagent_persists_structured_substitution_without_prefixing_result(
    tmp_path, monkeypatch
):
    import asyncio

    from gideon.engine.subagent import (
        DelegationSupervisor,
        SubagentInfo,
        _ExecutionPass,
    )
    from gideon.engine.subagent_persistence import create_agent_folder, read_state

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    info = SubagentInfo(
        id="substitution-check",
        task="Compare the options.",
        model="preferred-model",
        parent_session_key="dashboard:parent",
        agent="Analyst",
    )
    create_agent_folder(info.id, task=info.task, agent=info.agent, max_turns=5)
    run = _ExecutionPass(
        info=info,
        client=None,
        session_key="subagent:substitution-check",
        policy="ask",
        turn_limit=5,
        research=True,
    )
    event = AgentEvent(
        kind="model_substitution",
        text="Analyst ran on backup:large instead of preferred:small.",
        tool_meta={
            "model_substitution": {
                "requested": "preferred:small",
                "served": "backup:large",
                "why": "the preferred model timed out",
                "fix": "Review Settings → Models.",
                "who": "Analyst",
            }
        },
        served_model_ref="backup:large",
    )
    supervisor = object.__new__(DelegationSupervisor)
    supervisor._on_event = None
    asyncio.run(supervisor._consume_model_substitution(run, event))

    assert info.model == "preferred-model"
    assert info.model_substitutions[0]["served"] == "backup:large"
    assert read_state(info.id)["model_substitutions"] == info.model_substitutions
    payload = DelegationSupervisor._completion_payload(info, usage=True)
    assert payload["model_substitutions"] == info.model_substitutions
    assert payload["result"] == info.result


def test_fallback_formatting_keeps_the_actual_repair_path():
    from gideon.engine.agents.native.failover import ModelFailover

    notice = ModelFailover(
        requested="local:preferred", who="Analyst", fix="Review the model order in Settings → Models."
    ).substitution("cloud:backup", [("local:preferred", "the provider timed out")])
    assert notice.fix == "Review the model order in Settings → Models."
    assert notice.to_dict()["fix"] == notice.fix
    assert notice.fix in notice.notice()
