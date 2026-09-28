"""The native loop and session manager share the current Settings threshold."""

from __future__ import annotations

import json

from gideon.cognition.context_compaction import total_chars
from gideon.core.config import AppConfig
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.engine.session import ConversationDirectory, live_autocompact_pct
from gideon.integrations.llm.acp_agent import AcpAgentProvider


def _settings(home, pct: float) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"session": {"autocompact_pct": pct}}), encoding="utf-8"
    )


def _runtime() -> NativeAgentRuntime:
    provider = AcpAgentProvider(command=["/bin/true"])
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="Threshold test", model="local"),
        model_provider=provider,
        tool_providers=[],
    )


def _convo(rounds: int = 10) -> list[dict]:
    messages: list[dict] = [{"role": "user", "content": "first message"}]
    for index in range(rounds):
        messages.append(
            {
                "role": "assistant",
                "content": f"step {index}",
                "tool_calls": [
                    {
                        "id": f"c{index}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": f"c{index}", "content": "X" * 2000}
        )
    messages.extend(
        [
            {"role": "user", "content": "latest request"},
            {"role": "assistant", "content": "latest reply"},
        ]
    )
    return messages


def test_native_loop_reads_the_live_setting_for_each_open_chat(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    runtime = _runtime()

    _settings(home, 50)
    assert live_autocompact_pct() == 50
    runtime._messages = _convo()
    runtime._last_context_pct = 60
    before = total_chars(runtime._messages)
    runtime._maybe_compact()
    assert total_chars(runtime._messages) < before

    _settings(home, 85)
    runtime._messages = _convo()
    runtime._last_context_pct = 80
    before = total_chars(runtime._messages)
    runtime._maybe_compact()
    assert total_chars(runtime._messages) == before

    _settings(home, 75)
    assert live_autocompact_pct() == 75
    runtime._maybe_compact()
    assert total_chars(runtime._messages) < before


def test_native_compaction_does_not_trigger_session_recycling(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    _settings(home, 90)
    directory = ConversationDirectory(AppConfig())
    runtime = _runtime()
    runtime._last_context_pct = 92

    assert directory.check_context_usage("dashboard:chat", runtime) == 92
    assert directory._background_tasks == set()
    assert directory._compacting == set()
