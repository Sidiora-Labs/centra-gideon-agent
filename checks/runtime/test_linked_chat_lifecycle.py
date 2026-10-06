import time

import pytest


@pytest.mark.asyncio
async def test_linked_chat_continues_and_reports_compaction_and_terminal_outcomes(
    tmp_path, monkeypatch
):
    from gideon.cognition.history import ConversationLog
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.llm.events import (
        EVENT_COMPACTION_STATUS,
        AgentEvent,
    )
    from gideon.interfaces.dashboard.chat_persistence import (
        _rehydrate_session_from_history,
        save_session_to_history,
    )
    from gideon.interfaces.dashboard.chat_utils import (
        _broadcast_compaction_result,
        compaction_result_notice,
        linked_terminal_notice,
        schedule_linked_channel_notice,
    )
    from gideon.interfaces.dashboard.state import ConsoleState

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setattr("gideon.engine.session_map.config_dir", lambda: home)
    monkeypatch.setattr("gideon.interfaces.dashboard.state.config_dir", lambda: home)

    directory = ConversationDirectory(AppConfig())
    log = ConversationLog(base_dir=tmp_path / "history")
    state = ConsoleState(directory, start_time=time.time(), conversation_log=log)
    session = state.get_or_create_session("linked-chat")
    session.append("user", "Keep working in this thread")
    state.link_channel("linked-chat", "thread-42", "channel-9", "discord")
    save_session_to_history(state, session)

    assert directory.get_session_for_thread("thread-42") == "dashboard:linked-chat"
    assert state.get_linked_session("thread-42") is session
    assert state.channel_provider_for("dashboard:linked-chat") == "discord"

    restored_state = ConsoleState(
        directory, start_time=time.time(), conversation_log=log
    )
    restored = _rehydrate_session_from_history(restored_state, "dashboard:linked-chat")
    assert restored is not None
    assert restored._channel_thread_ts == "thread-42"
    assert restored_state.get_linked_session("thread-42") is restored
    assert restored_state.channel_provider_for("dashboard:linked-chat") == "discord"

    expected = {
        "completed": "Conversation compacted: retained context",
        "noop": "Conversation is too short to compact; nothing changed.",
        "failed": "Compaction failed: provider unavailable",
    }
    for status, notice in expected.items():
        result = _broadcast_compaction_result(
            restored_state,
            restored,
            AgentEvent(
                kind=EVENT_COMPACTION_STATUS,
                text=status,
                title=(
                    "retained context"
                    if status == "completed"
                    else "provider unavailable" if status == "failed" else ""
                ),
            ),
        )
        assert result == notice

    assert compaction_result_notice("noop", "") == expected["noop"]
    assert (
        compaction_result_notice("failed", "provider unavailable") == expected["failed"]
    )
    assert compaction_result_notice("timeout") == "Compaction timed out."

    state.wire_session_compact_callback()
    await directory._on_compacted("dashboard:linked-chat", 82.0)
    assert session.messages[-1]["content"] == "🔄 Auto-compacted at 82%."
    assert linked_terminal_notice("stopped") == "Turn stopped at your request."
    assert linked_terminal_notice("error", "runtime reset") == (
        "Turn ended without a reply: runtime reset"
    )

    before = len(restored_state._background_tasks)
    schedule_linked_channel_notice(
        restored_state, restored, "Turn stopped at your request."
    )
    assert len(restored_state._background_tasks) == before

    await directory.close_all()
