"""Provider policy vectors using the actual configuration and protocol value types."""

import sys
from pathlib import Path

import pytest

from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.types import EVENT_TOOL_CALL, EVENT_TOOL_RESULT, AcpEvent
from gideon.integrations.llm.acp_agent import AcpAgentProvider, _factory
from gideon.integrations.llm.acp_provider_runtime import (
    ProbePlan,
    capability_names,
    relay_events,
)
from gideon.integrations.llm.registry import ProviderEntry, ProviderResolutionError


def test_capability_names_preserve_non_boolean_declarations():
    assert capability_names(
        {"yes": True, "no": False, "zero": 0, "empty": {}, 9: None}
    ) == frozenset({"yes", "zero", "empty", "9"})
    assert capability_names([]) == frozenset()


@pytest.mark.parametrize("command", [None, [], "agent", ("agent",)])
def test_invalid_launch_commands_are_rejected_before_client_construction(command):
    entry = ProviderEntry(
        name="acp:selection", type="acp_agent", model="", options={"command": command}
    )
    with pytest.raises(
        ProviderResolutionError, match="requires a non-empty options.command list"
    ):
        _factory(entry=entry)
    assert ProbePlan.from_options(entry.options) is None


def test_session_settings_take_precedence_and_bind_the_real_client(tmp_path):
    entry = ProviderEntry(
        name="acp:selection",
        type="acp_agent",
        model="entry-model",
        options={
            "command": [sys.executable, "--version"],
            "cwd": str(tmp_path / "entry"),
            "env": {"COUNT": 3},
            "agent_name": "entry-agent",
            "channel_id": "entry-channel",
            "sandbox": "entry-sandbox",
            "capability_flags": {"sample": 1},
        },
    )
    provider = _factory(
        entry=entry,
        session_key="session",
        cwd=tmp_path / "session",
        model="session-model",
        agent="session-agent",
        channel_id="session-channel",
        sandbox="none",
        reasoning_effort_override="high",
    )
    assert isinstance(provider.client, AcpClient)
    assert provider.provider_id == "acp:selection"
    assert provider.agent_model == "session-model"
    assert provider.agent_name == "session-agent"
    assert provider._cwd == tmp_path / "session"
    assert provider._env == {"COUNT": "3"}
    assert provider._channel_id == "session-channel"
    assert provider._sandbox == "none"
    assert provider._reasoning_effort == "high"
    assert provider._capability_flags == {"sample": True}
    assert provider.client._work_dir == tmp_path / "session"
    provider.set_workspace(tmp_path / "rebound")
    assert provider.client._work_dir == provider._cwd == tmp_path / "rebound"
    provider.set_session_key("next", "room")
    assert provider._session_key == "next" and provider._channel_id == "room"
    assert provider.session_id == "" and provider.resumed is False


def test_runtime_identity_fallback_does_not_interpret_the_command_basename():
    provider = AcpAgentProvider(command=["/tmp/acp:executable"])
    assert provider.provider_id == "acp:acp:executable"


def test_real_executable_preflight_checks_the_declared_delegate():
    present = ProbePlan.from_options({"command": [sys.executable]})
    assert present.preflight() is None
    absent = ProbePlan.from_options(
        {
            "command": [sys.executable],
            "requires_executable": {
                "label": "__gideon_absent_delegate_9c273__",
                "env_var": "CUSTOM_ENGINE_BIN",
            },
        }
    )
    refused = absent.preflight()
    assert refused.state == "not_found"
    assert "CUSTOM_ENGINE_BIN" in refused.detail
    declared = ProbePlan.from_options(
        {
            "command": [sys.executable],
            "requires_executable": {
                "label": "__gideon_absent_delegate_9c273__",
                "path": sys.executable,
            },
        }
    )
    assert declared.preflight() is None


@pytest.mark.parametrize(
    ("error", "state", "login"),
    [
        (TimeoutError(), "timeout", ["sign-in", "now"]),
        (RuntimeError("Authentication required"), "needs_login", ["sign-in", "now"]),
        (ValueError("Unsupported protocol"), "error", None),
    ],
)
def test_probe_failure_classification_keeps_timeout_and_auth_distinct(
    error, state, login
):
    plan = ProbePlan.from_options(
        {
            "command": [sys.executable],
            "login_command": ["sign-in", "now"],
            "probe_timeout_secs": 7,
        }
    )
    failure = plan.failure(error)
    assert failure.ready is False and failure.state == state
    assert failure.login_command == login
    if state == "timeout":
        assert "7s" in failure.detail


def test_probe_uses_only_the_executable_as_login_fallback():
    plan = ProbePlan.from_options({"command": [sys.executable, "--stdio"]})
    assert plan.failure(RuntimeError("unauthorized")).login_command == [sys.executable]


def test_discovery_normalizes_real_session_axes_without_creating_effort_agents():
    discovered = AcpAgentProvider.agents_from_snapshot(
        {
            "runtime_id": "acp:selection",
            "runtime_label": "Selected",
            "dialect": "claude-code",
        },
        {
            "configOptions": [
                {"id": "model", "options": [{"value": "default"}, {"value": "chosen"}]},
                {
                    "id": "effort",
                    "options": [
                        {"value": "default", "name": "Default"},
                        {"value": "high", "name": "High"},
                    ],
                },
            ],
        },
    )
    assert len(discovered) == 1
    assert discovered[0].id == "acp:selection"
    assert discovered[0].name == "Selected"
    assert discovered[0].models == ["default", "chosen"]
    assert discovered[0].supported_efforts == [{"value": "high", "label": "High"}]


@pytest.mark.asyncio
async def test_new_client_has_no_turn_to_cancel():
    provider = AcpAgentProvider(command=[sys.executable])
    assert await provider.cancel(wait_ack_timeout=1) == "no_turn"
    assert provider.declared_capabilities == frozenset()


@pytest.mark.asyncio
async def test_event_relay_folds_real_protocol_values_and_resets_between_turns():
    provider = AcpAgentProvider(command=[sys.executable])
    events = [
        AcpEvent(kind=EVENT_TOOL_CALL, tool_call_id="one", title="Read"),
        AcpEvent(kind=EVENT_TOOL_RESULT, tool_call_id="one"),
    ]

    async def records(_message):
        for event in events:
            yield event

    received = [event async for event in relay_events(provider, records, "first")]
    assert [event.kind for event in received] == [EVENT_TOOL_CALL, EVENT_TOOL_RESULT]
    assert provider.drain_tool_outcomes() == [("Read", "success")]
    events[:] = [AcpEvent(kind=EVENT_TOOL_RESULT, tool_call_id="one")]
    [event async for event in relay_events(provider, records, "second")]
    assert provider.drain_tool_outcomes() == []
