import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.turn_source import arrived_on, shared_source, source_of
from gideon.integrations.inbox_providers.native_source import (
    get_dashboard_state,
    set_dashboard_state,
)
from gideon.integrations.llm_helpers import save_conversation_turn
from gideon.interfaces.dashboard.chat_fork import (
    api_chat_session_fork,
    api_chat_session_fork_rewound,
)
from gideon.interfaces.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    save_session_to_history,
)
from gideon.interfaces.dashboard.chat_utils import _dequeue_next_message
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}')
    current = ConsoleState(
        sessions=ConversationDirectory(AppConfig.load()),
        start_time=0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    previous = get_dashboard_state()
    set_dashboard_state(current)
    yield current
    set_dashboard_state(previous)


def test_dashboard_source_and_absent_copied_source_are_distinct():
    session = _ChatSession("one")
    session.append("user", "Typed here")
    session.append("user", "Copied old row", source={})
    assert source_of(session.messages[0]) == arrived_on("dashboard", "dashboard")
    assert source_of(session.messages[1]) == {}
    assert source_of({"source_thread": "room", "source_user": 123}) == {
        "source_thread": "room"
    }


def test_saved_restored_rows_keep_sender_thread_and_real_event_ids(state):
    log = state.conversation_log
    event = log.append(
        "dashboard:kept",
        "user",
        "Question",
        source_thread="channel-thread",
        source_user="alice",
    )
    session = _rehydrate_session_from_history(state, "kept")
    assert source_of(session.messages[0]) == {
        **arrived_on("channel-thread", "alice"),
        "source_event_id": event.source_event_id,
    }
    session.append("assistant", "Answer")
    save_session_to_history(state, session, force=True)
    assert log.source_events("dashboard:kept")[0].raw_bytes == event.raw_bytes
    state._sessions.pop("kept")
    restarted = _rehydrate_session_from_history(state, "kept")
    assert source_of(restarted.messages[0])["source_user"] == "alice"
    assert log.source_events("dashboard:kept")[0].source_digest == event.source_digest


def test_labels_never_create_actor_authority_or_replace_question_metadata(state):
    session = state.get_or_create_session("kept")
    session._initiator = {"kind": "unknown"}
    metadata = {"owner_question": {"id": "q1", "state": "waiting"}}
    session.append("user", "Text", source=arrived_on("room", "owner"), meta=metadata)
    save_session_to_history(state, session, force=True)
    assert session._initiator == {"kind": "unknown"}
    assert "ingress" not in session.messages[0]["meta"]
    assert state.conversation_log.read_messages("dashboard:kept")[0]["meta"] == metadata


def test_queue_merges_only_shared_display_source_and_keeps_original_rows():
    session = _ChatSession("queue")
    session.queue_append("first", source=arrived_on("thread-a", "alice"))
    session.queue_append("second", source=arrived_on("thread-a", "alice"))
    text, consumed = _dequeue_next_message(session, merge_enabled=True)
    session.append("user", text, source=shared_source(consumed))
    assert source_of(session.messages[-1]) == arrived_on("thread-a", "alice")
    assert shared_source([{"source_thread": "a"}, {"source_thread": "b"}]) == {}
    assert source_of(consumed[0]) == source_of(consumed[1])


@pytest.mark.asyncio
async def test_real_fork_and_rewound_fork_keep_row_labels_and_metadata(state):
    session = state.get_or_create_session("kept")
    session.append(
        "user",
        "Original",
        source=arrived_on("old-thread", "alice"),
        meta={"owner_question": {"id": "q1"}},
    )
    session.append("assistant", "Answer")
    save_session_to_history(state, session, force=True)
    app = web.Application()
    app["state"] = state
    app.router.add_post("/sessions/{session}/fork", api_chat_session_fork)
    app.router.add_post("/sessions/{session}/rewound", api_chat_session_fork_rewound)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/sessions/kept/fork", json={})
        assert response.status == 200
        body = await response.json()
        fork = state._sessions[body["key"]]
        assert source_of(fork.messages[0]) == arrived_on("old-thread", "alice")
        assert fork.messages[0]["meta"]["owner_question"]["id"] == "q1"
        session.messages[0]["rewound"] = [
            {
                "messages": [dict(session.messages[0]), dict(session.messages[1])],
                "ts": "2026-10-06T00:00:00Z",
            }
        ]
        response = await client.post("/sessions/kept/rewound", json={"index": 0})
        assert response.status == 200
        tail = state._sessions[(await response.json())["key"]]
        assert source_of(tail.messages[0]) == arrived_on("old-thread", "alice")
        assert tail.messages[0]["meta"]["owner_question"]["id"] == "q1"
    finally:
        await client.close()


def test_native_channel_writer_mirrors_actual_journal_events_without_text_dedup(state):
    session = state.get_or_create_session("kept")
    save_conversation_turn(
        state.conversation_log,
        "dashboard:kept",
        "Same text",
        "Same text",
        "thread",
        "alice",
    )
    save_conversation_turn(
        state.conversation_log,
        "dashboard:kept",
        "Same text",
        "Same text",
        "thread",
        "alice",
    )
    original = state.conversation_log.source_events("dashboard:kept")
    assert len(session.messages) == 4
    assert len({row["source_event_id"] for row in session.messages}) == 4
    session.append("user", "Local next")
    save_session_to_history(state, session, force=True)
    final = state.conversation_log.source_events("dashboard:kept")
    assert [event.raw_bytes for event in final[:4]] == [
        event.raw_bytes for event in original
    ]
    assert all(row["source_user"] == "alice" for row in session.messages[:4])


def test_concurrent_native_append_and_save_do_not_drop_journal_events(state):
    session = state.get_or_create_session("kept")
    session.append("user", "Initial")
    save_session_to_history(state, session, force=True)
    log = state.conversation_log

    def append(index):
        return log.append(
            "dashboard:kept",
            "user",
            "Same text",
            source_thread="thread",
            source_user="alice",
        )

    def save(index):
        try:
            save_session_to_history(state, session, force=True)
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(append if n % 2 else save, n) for n in range(40)]
        results = [future.result() for future in futures]
    events = [result for result in results if hasattr(result, "source_event_id")]
    state.take_channel_turn(log, "dashboard:kept")
    session.append("user", "Local next")
    save_session_to_history(state, session, force=True)
    final = log.source_events("dashboard:kept")
    assert len(final) == 22
    final_ids = {event.source_event_id for event in final}
    assert all(event.source_event_id in final_ids for event in events)
    assert len([row for row in session.messages if row["content"] == "Same text"]) == 20
