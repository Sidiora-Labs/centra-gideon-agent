"""Real prompt assembly keeps dashboard history and profile layers isolated."""

import json

import pytest

from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import ConversationLog
from gideon.cognition.memory import MemoryJournal
from gideon.engine.agents.defaults import DEFAULT_NATIVE_SYSTEM_PROMPT
from gideon.extensions.skills import ProcedureLibrary
from gideon.interfaces.dashboard.chat_persistence import prior_turns_transcript
from gideon.interfaces.dashboard.state import _ChatSession


@pytest.fixture(autouse=True)
def isolated_prompt_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    import gideon.integrations.prompt_providers.registry as registry

    registry._providers.clear()
    yield
    registry._providers.clear()


def _builder(tmp_path, log):
    return PromptAssembler(
        memory=MemoryJournal(workspace=tmp_path / "memory"),
        skills=ProcedureLibrary(
            skills_path=tmp_path / "skills", install_builtins=False
        ),
        conversation_log=log,
    )


def test_live_session_history_stops_before_current_and_excludes_cancelled(tmp_path):
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    session = _ChatSession("dashboard:first")
    session.append("user", "completed question")
    session.append("assistant", "completed answer")
    session.append("user", "cancelled question")
    session.append("assistant", "partial cancelled output")
    stop = {
        "kind": "stop_event",
        "state": "stopped",
        "outcome": "soft",
    }
    session.append("system", json.dumps(stop), json.dumps(stop))
    session.append("user", "current question")

    prior = prior_turns_transcript(session, "current question")
    assert prior == [
        {"role": "user", "content": "completed question"},
        {"role": "assistant", "content": "completed answer"},
    ]

    assembled, _ = _builder(tmp_path, log).build_message(
        "current question",
        True,
        session_key="dashboard:first",
        prior_transcript=prior,
    )
    assert assembled.count("completed question") == 1
    assert assembled.count("completed answer") == 1
    assert assembled.count("current question") == 1
    assert "partial cancelled output" not in assembled
    assert "cancelled question" not in assembled


def test_dashboard_chat_does_not_import_a_neighbor_session(tmp_path):
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    log.append("dashboard:other", "user", "other-chat-private-marker")
    log.append("dashboard:other", "assistant", "other-chat-response")
    session = _ChatSession("dashboard:this")
    session.append("user", "this-chat-question")

    assembled, _ = _builder(tmp_path, log).build_message(
        "this-chat-question",
        True,
        session_key="dashboard:this",
        prior_transcript=prior_turns_transcript(session, "this-chat-question"),
    )
    assert "other-chat-private-marker" not in assembled
    assert "other-chat-response" not in assembled
    assert assembled.count("this-chat-question") == 1


def test_active_chat_prompt_and_agent_voice_are_layered_with_policy(tmp_path):
    from gideon.extensions.providers.prompt_use_cases import save_active_prompts
    from gideon.integrations.prompt_providers.base import PromptTemplate
    from gideon.integrations.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()
    provider = get_prompt_provider("native")
    assert provider is not None
    provider.create_prompt(
        PromptTemplate(name="bound-chat", content="BOUND CHAT OPERATING PROMPT")
    )
    provider.create_prompt(
        PromptTemplate(name="updated-chat", content="UPDATED CHAT OPERATING PROMPT")
    )
    save_active_prompts({"chat": "native:bound-chat"})

    from gideon.core.config.loader import AgentProfile, AppConfig, resolve_agent_bindings

    config = AppConfig()
    config.default_agent = "Gideon"
    config.agents = {
        "Gideon": AgentProfile(
            system_prompt="", voice="Warm, concise, and candid."
        )
    }
    bindings = resolve_agent_bindings(config, "Gideon")

    from gideon.integrations.natural_voice import maybe_inject

    dispatched = maybe_inject("hello", "on")
    assert dispatched != "hello"
    assert dispatched.count("hello") == 1
    assembled, _ = _builder(tmp_path, None).build_message(
        dispatched,
        True,
        session_key="dashboard:voice",
        agent="Gideon",
        system_prompt_override=bindings.system_prompt,
        agent_voice=bindings.voice,
    )
    assert "BOUND CHAT OPERATING PROMPT" in assembled
    assert "[NATURAL VOICE]" in assembled
    assert "Warm, concise, and candid." in assembled
    assert "[CRITICAL RULES" in assembled
    assert DEFAULT_NATIVE_SYSTEM_PROMPT not in assembled

    save_active_prompts({"chat": "native:updated-chat"})
    next_turn, _ = _builder(tmp_path, None).build_message(
        "next question",
        False,
        session_key="dashboard:voice",
        agent="Gideon",
        system_prompt_override=bindings.system_prompt,
        agent_voice="More direct and playful.",
    )
    assert "UPDATED CHAT OPERATING PROMPT" in next_turn
    assert "More direct and playful." in next_turn
    assert "BOUND CHAT OPERATING PROMPT" not in next_turn
    assert next_turn.count("next question") == 1


def test_only_exact_seeded_default_prompt_is_retired():
    from gideon.core.config.loader import AgentProfile, AppConfig
    from gideon.core.config.migrations import _retire_seeded_native_prompt

    config = AppConfig()
    config.default_agent = "Gideon"
    config.agents = {
        "Gideon": AgentProfile(system_prompt=DEFAULT_NATIVE_SYSTEM_PROMPT),
        "Custom": AgentProfile(system_prompt="A user-authored operating prompt."),
    }

    assert _retire_seeded_native_prompt(config)
    assert config.agents["Gideon"].system_prompt == ""
    assert config.agents["Custom"].system_prompt == "A user-authored operating prompt."

    from gideon.core.config.loader import resolve_agent_bindings

    config.agents["Gideon"].system_prompt = "User-authored default operating prompt."
    config.agents["Gideon"].voice = "User-authored default voice."
    binding = resolve_agent_bindings(config, "Gideon")
    assert binding.system_prompt == "User-authored default operating prompt."
    assert binding.voice == "User-authored default voice."
