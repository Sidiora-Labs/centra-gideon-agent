"""The durable chat index (req 20.1) — markers that survive a restart and land where a fork would.

The index is derived from the transcript, written into the session's metadata line and read back
by every restore path. Its ``jump_index`` is not a new coordinate: it is the ``at_message_index``
``POST /api/chat/sessions/{s}/fork`` takes, so the fork route itself is driven here rather than
asserting the derivation against itself.

``fixtures/chat_index_parity.json`` is shared with the console's
``src/features/chat/sessionMarkers.test.ts`` — one transcript, one expected marker list, both
derivations. It is the only thing that keeps the two ports from drifting.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from gideon.cognition.history import ConversationLog
from gideon.interfaces.dashboard.chat_index import (
    INDEX_CAP,
    build_chat_index,
    load_chat_index,
)
from gideon.interfaces.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    restore_recent_sessions,
    save_session_to_history,
)
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession

SESSION = "chat-index-1"
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "chat_index_parity.json").read_text(
        encoding="utf-8"
    )
)


def _state(tmp_path: Path) -> ConsoleState:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    sessions.set_task_mode = MagicMock()
    return ConsoleState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )


def _session(state: ConsoleState, messages: list[dict]) -> _ChatSession:
    s = state.get_or_create_session(SESSION)
    for i, m in enumerate(messages):
        s.messages.append({**m, "ts": f"2026-09-05T10:00:{i:02d}"})
    return s


def _meta(tmp_path: Path) -> dict:
    files = list(tmp_path.rglob("*.jsonl"))
    assert files, "nothing was persisted"
    return json.loads(files[0].read_text().splitlines()[0])


def _kinds(index: list[dict]) -> list[str]:
    return [e["kind"] for e in index]


class TestDerivation:
    """One marker per turn, plus every tool/subagent/approval/error sub-event."""

    def test_a_turn_only_session_is_one_marker_per_turn(self):
        index = build_chat_index(
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "two"},
                {"role": "assistant", "content": "second answer"},
            ]
        )
        assert _kinds(index) == ["turn"] * 4
        assert [e["label"] for e in index] == [
            "one",
            "first answer",
            "two",
            "second answer",
        ]
        assert [e["jump_index"] for e in index] == [0, 1, 2, 3]
        assert not any(e["failed_tool"] for e in index)

    def test_sub_events_carry_the_owning_turns_coordinate(self):
        index = build_chat_index(
            [
                {"role": "user", "content": "go"},
                {"role": "tool", "content": "Read", "meta": {"tool_call_id": "t1"}},
                {
                    "role": "permission",
                    "content": "Terminal",
                    "meta": {"approval_id": "a1", "tool": "Terminal"},
                },
                {"role": "assistant", "content": "part one"},
                {"role": "assistant", "content": "part two"},
            ]
        )
        assert _kinds(index) == ["turn", "turn", "tool", "approval"]
        assert [e["jump_index"] for e in index] == [0, 2, 2, 2]

    def test_a_failed_tool_marks_the_whole_turn(self):
        index = build_chat_index(
            [
                {"role": "user", "content": "build"},
                {"role": "tool", "content": "Read", "meta": {"tool_call_id": "t1"}},
                {
                    "role": "tool",
                    "content": "Terminal",
                    "meta": {"tool_call_id": "t2", "ok": False},
                },
                {"role": "assistant", "content": "broke"},
                {"role": "user", "content": "again"},
                {"role": "assistant", "content": "fine"},
            ]
        )
        failed = {e["id"] for e in index if e["failed_tool"]}
        assert failed == {"t1", "t1s0", "t1s1"}
        assert not any(e["failed_tool"] for e in index if e["turn_index"] > 1)

    def test_an_agent_error_on_a_later_frame_of_the_same_call_still_fails_it(self):
        index = build_chat_index(
            [
                {"role": "user", "content": "go"},
                {"role": "tool", "content": "Read", "meta": {"tool_call_id": "t1"}},
                {
                    "role": "tool",
                    "content": "Read",
                    "meta": {
                        "tool_call_id": "t1",
                        "done": True,
                        "agent_error": {
                            "code": "ENOENT",
                            "what": "missing",
                            "why": "no path",
                            "fix": "check it",
                        },
                    },
                },
                {"role": "assistant", "content": "could not"},
            ]
        )
        assert _kinds(index) == ["turn", "turn", "tool"], "the repeat frame was doubled"
        assert all(e["failed_tool"] for e in index if e["turn_index"] == 1)

    def test_a_delegating_tool_reads_as_a_subagent(self):
        index = build_chat_index(
            [
                {"role": "user", "content": "delegate"},
                {
                    "role": "tool",
                    "content": "Task",
                    "meta": {"tool_call_id": "t1", "tool": "Task"},
                },
                {
                    "role": "tool",
                    "content": "Read",
                    "meta": {"tool_call_id": "t2", "tool": "Read"},
                },
                {"role": "assistant", "content": "done"},
            ]
        )
        assert _kinds(index) == ["turn", "turn", "subagent", "tool"]

    def test_an_empty_transcript_has_no_markers(self):
        assert build_chat_index([]) == []

    def test_the_index_is_capped_at_the_newest_markers(self):
        messages: list[dict] = []
        for i in range(INDEX_CAP + 40):
            messages.append({"role": "user", "content": f"q{i}"})
            messages.append({"role": "assistant", "content": f"a{i}"})
        index = build_chat_index(messages)
        assert len(index) == INDEX_CAP
        assert index[-1]["label"] == f"a{INDEX_CAP + 39}"


class TestSharedVector:
    """The fixture the console test reads too — collapsed re-injection, a merged answer,
    a deduped failing call, an emoji-prefixed tool name, an approval and an error."""

    def test_python_derives_the_shared_vector_exactly(self):
        assert build_chat_index(FIXTURE["messages"]) == FIXTURE["expected"]

    def test_the_vector_actually_exercises_the_hard_cases(self):
        assert _kinds(FIXTURE["expected"]) == [
            "turn",
            "turn",
            "tool",
            "subagent",
            "approval",
            "error",
            "turn",
            "turn",
        ]
        merged = next(e for e in FIXTURE["expected"] if e["id"] == "t1")
        assert merged["jump_index"] != merged["turn_index"], (
            "a merged assistant turn is where the naive index and the fork coordinate "
            "part company — the vector must keep that case"
        )


class TestDurability:
    """Written with the session, read back by both restore paths."""

    def test_the_index_is_written_to_the_meta_line(self, tmp_path):
        state = _state(tmp_path)
        save_session_to_history(state, _session(state, FIXTURE["messages"]))
        assert _meta(tmp_path)["chat_index"] == FIXTURE["expected"]

    def test_the_live_session_holds_the_same_index_it_wrote(self, tmp_path):
        state = _state(tmp_path)
        session = _session(state, FIXTURE["messages"])
        save_session_to_history(state, session)
        assert session.chat_index == FIXTURE["expected"]

    def test_the_bulk_restart_restore_brings_it_back(self, tmp_path):
        state = _state(tmp_path)
        save_session_to_history(state, _session(state, FIXTURE["messages"]))

        fresh = _state(tmp_path)
        assert restore_recent_sessions(fresh) == 1
        restored = fresh._sessions[SESSION]
        assert restored.chat_index == FIXTURE["expected"]

    def test_the_targeted_rehydrate_brings_it_back(self, tmp_path):
        state = _state(tmp_path)
        save_session_to_history(state, _session(state, FIXTURE["messages"]))

        fresh = _state(tmp_path)
        restored = _rehydrate_session_from_history(fresh, SESSION)
        assert restored is not None
        assert restored.chat_index == FIXTURE["expected"]

    def test_a_reloaded_index_still_indexes_the_transcript_that_came_back(
        self, tmp_path
    ):
        """The point of persisting it: after a restart the markers and the restored
        messages describe the same conversation, so a marker still resolves."""
        state = _state(tmp_path)
        save_session_to_history(state, _session(state, FIXTURE["messages"]))

        fresh = _state(tmp_path)
        restored = _rehydrate_session_from_history(fresh, SESSION)
        assert restored is not None
        visible = [
            m for m in restored.messages if m.get("role") in ("user", "assistant")
        ]
        for entry in restored.chat_index:
            assert 0 <= entry["jump_index"] < len(visible)
        last = restored.chat_index[-1]
        assert visible[last["jump_index"]]["content"] == "welcome"

    def test_a_second_save_refreshes_rather_than_keeps_a_stale_index(self, tmp_path):
        state = _state(tmp_path)
        session = _session(state, FIXTURE["messages"])
        save_session_to_history(state, session)
        session.messages.append(
            {"role": "user", "content": "one more", "ts": "2026-09-05T10:30:00"}
        )
        save_session_to_history(state, session)
        index = _meta(tmp_path)["chat_index"]
        assert index[-1]["label"] == "one more"
        assert index[-1]["jump_index"] == 6

    def test_a_session_with_nothing_to_index_writes_no_key(self, tmp_path):
        state = _state(tmp_path)
        session = _session(state, [{"role": "system", "content": "booted"}])
        save_session_to_history(state, session)
        assert "chat_index" not in _meta(tmp_path)

    def test_a_hand_edited_index_is_dropped_rather_than_trusted(self, tmp_path):
        state = _state(tmp_path)
        save_session_to_history(state, _session(state, FIXTURE["messages"]))
        path = list(tmp_path.rglob("*.jsonl"))[0]
        lines = path.read_text().splitlines()
        meta = json.loads(lines[0])
        meta["chat_index"] = [
            {**FIXTURE["expected"][0], "jump_index": "not an int"},
            {"kind": "turn"},
            "not a marker",
            FIXTURE["expected"][1],
        ]
        path.write_text("\n".join([json.dumps(meta), *lines[1:]]) + "\n")

        fresh = _state(tmp_path)
        restored = _rehydrate_session_from_history(fresh, SESSION)
        assert restored is not None
        assert restored.chat_index == [FIXTURE["expected"][1]]

    def test_load_rejects_a_bool_where_a_coordinate_belongs(self):
        assert load_chat_index([{**FIXTURE["expected"][0], "jump_index": True}]) == []
        assert load_chat_index("nonsense") == []


class TestForkCoordinateIsTheSameCoordinate:
    """req 20.1's "same navigation coordinates used by forking" — driven through the
    fork route, not asserted against the derivation that produced it."""

    @pytest.mark.asyncio
    async def test_forking_at_a_marker_copies_exactly_that_turn(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = _state(tmp_path)
        session = _session(state, FIXTURE["messages"])
        save_session_to_history(state, session)
        session._dirty = False
        session._resumed_count = len(session.messages)

        marker = next(e for e in session.chat_index if e["id"] == "t1")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                f"/api/chat/sessions/{SESSION}/fork",
                json={"at_message_index": marker["jump_index"]},
            )
            assert resp.status == 200
            forked = await resp.json()

        copied = state._sessions[forked["key"]].messages
        assert forked["messages"] == marker["jump_index"] + 1
        assert [m["content"] for m in copied] == [
            "fix the build",
            "fix the build",
            "step one",
            "step two",
        ]
        assert copied[-1]["content"] == "step two", (
            "the merged assistant turn's marker must land on its LAST message, "
            "which is what forking that turn copies"
        )

    @pytest.mark.asyncio
    async def test_the_detail_route_hands_the_index_to_the_surface(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = _state(tmp_path)
        save_session_to_history(state, _session(state, FIXTURE["messages"]))

        fresh = _state(tmp_path)
        restore_recent_sessions(fresh)
        async with TestClient(TestServer(_make_app(fresh))) as client:
            data = await (await client.get(f"/api/chat/sessions/{SESSION}")).json()
        assert data["chat_index"] == FIXTURE["expected"]
