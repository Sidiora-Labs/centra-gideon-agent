"""Tests for dashboard hook handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gideon.cognition.history import ConversationLog
from gideon.engine.hooks import (
    HOOK_EVENT_AGENT_SPAWN,
    HOOK_EVENT_USER_PROMPT_SUBMIT,
    ScriptHookStore,
)
from gideon.interfaces.dashboard.state import ConsoleState


def _make_state(tmp_path):
    """Create a ConsoleState wired for run_chat hook tests."""
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    state = ConsoleState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.context_builder = None
    return state


class TestHookHandlerIntegration:
    """Integration tests for hook store used by dashboard handlers."""

    def test_create_and_retrieve_hook(self, tmp_path):
        """Hook CRUD operations work through store."""
        store = ScriptHookStore(tmp_path)

        hook = store.create(
            {
                "name": "test-hook",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "command": "echo test",
                "timeout": 30,
            }
        )
        assert hook.id is not None

        retrieved = store.get(hook.id)
        assert retrieved is not None
        assert retrieved.name == "test-hook"

        updated = store.update(hook.id, {"name": "updated-name"})
        assert updated.name == "updated-name"

        assert store.delete(hook.id) is True
        assert store.get(hook.id) is None


class TestAgentSpawnHookInjection:
    """AgentSpawn hook stdout must be injected into session context on new sessions."""

    @pytest.mark.asyncio
    async def test_injects_on_new_session(self, tmp_path, monkeypatch):
        from gideon.integrations.llm.base import LLMEvent
        from gideon.interfaces.dashboard.chat import run_chat

        monkeypatch.setattr(
            "gideon.interfaces.dashboard.chat.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr("gideon.interfaces.dashboard.chat.sel", lambda: MagicMock())

        state = _make_state(tmp_path)
        hook_store = ScriptHookStore(config_dir=tmp_path)
        hook = hook_store.create(
            {
                "name": "startup-prefs",
                "event": HOOK_EVENT_AGENT_SPAWN,
                "provider": "bash",
                "provider_config": {"command": "echo 'Enable caveman mode'"},
                "timeout": 5,
            }
        )
        state._hook_store = hook_store
        bindings = MagicMock(triggers=[hook.id])
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.chat_runner.resolve_agent_bindings",
            lambda *a, **k: bindings,
        )

        captured_message = None
        fake_client = AsyncMock()

        async def _stream(msg):
            nonlocal captured_message
            captured_message = msg
            yield LLMEvent(kind="text_chunk", text="ok")
            yield LLMEvent(kind="complete")

        fake_client.stream = _stream
        fake_client.stream_command = _stream
        fake_client.context_usage_pct = MagicMock(return_value=0.0)
        state.sessions.get_or_create = AsyncMock(
            return_value=(fake_client, True, False)
        )

        session = state.get_or_create_session("s1")
        await run_chat(state, session, "hello")

        assert captured_message is not None
        assert "[Hook context]" in captured_message
        assert "Enable caveman mode" in captured_message

    @pytest.mark.asyncio
    async def test_not_injected_on_existing_session(self, tmp_path, monkeypatch):
        from gideon.integrations.llm.base import LLMEvent
        from gideon.interfaces.dashboard.chat import run_chat

        monkeypatch.setattr(
            "gideon.interfaces.dashboard.chat.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr("gideon.interfaces.dashboard.chat.sel", lambda: MagicMock())

        state = _make_state(tmp_path)
        hook_store = ScriptHookStore(config_dir=tmp_path)
        hook_store.create(
            {
                "name": "startup-prefs",
                "event": HOOK_EVENT_AGENT_SPAWN,
                "provider": "bash",
                "provider_config": {"command": "echo 'Enable caveman mode'"},
                "timeout": 5,
            }
        )
        state._hook_store = hook_store

        captured_message = None
        fake_client = AsyncMock()

        async def _stream(msg):
            nonlocal captured_message
            captured_message = msg
            yield LLMEvent(kind="text_chunk", text="ok")
            yield LLMEvent(kind="complete")

        fake_client.stream = _stream
        fake_client.stream_command = _stream
        fake_client.context_usage_pct = MagicMock(return_value=0.0)
        state.sessions.get_or_create = AsyncMock(
            return_value=(fake_client, False, False)
        )

        session = state.get_or_create_session("s1")
        await run_chat(state, session, "hello")

        assert captured_message is not None
        assert "Enable caveman mode" not in captured_message
