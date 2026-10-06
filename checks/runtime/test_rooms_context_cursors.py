import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms.cursors import (
    RoomCursorError,
    advance,
    cursor_for,
    cursors_path,
    member_feed,
    read_cursors,
)
from gideon.engine.rooms.store import RoomStore
from gideon.engine.rooms.turn import MemberTurn, RoomTurns
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_AGENT_ID", raising=False)
    monkeypatch.delenv("GIDEON_SESSION_KEY", raising=False)

    (tmp_path / "config.json").write_text('{"rooms":{"enabled":true}}')
    return tmp_path


def room(store):
    return store.create(
        "Review",
        [
            {"id": name, "agent": "default", "name": name.title()}
            for name in ("analyst", "skeptic")
        ],
    )


def test_members_read_different_unseen_tails_and_fresh_sessions_replay(home):
    store = RoomStore(home)
    created = room(store)
    for text in ("should we ship?", "what about cost?", "welcome back"):
        store.append(created.id, "user", text, speaker="user")
    advance(store, created.id, "analyst", 1)
    advance(store, created.id, "skeptic", 2)
    messages = store.messages(created.id)
    feeds = [
        member_feed(
            messages, cursor_for(store, created.id, name), name, remembers=True
        )[0]
        for name in ("analyst", "skeptic")
    ]
    assert [row["content"] for row in feeds[0]] == ["what about cost?", "welcome back"]
    assert [row["content"] for row in feeds[1]] == ["welcome back"]
    assert member_feed(messages, 2, "skeptic", remembers=False) == (messages, False)
    assert member_feed(messages, 99, "skeptic", remembers=True) == (messages, False)
    assert member_feed(messages, 0, "skeptic", remembers=True) == (messages, False)


def test_success_acknowledges_only_read_boundary_and_omits_own_live_answer(home):
    store = RoomStore(home)
    created = room(store)
    store.begin_turn(
        created.id, "should we ship?", "round", pending_queue=["analyst", "skeptic"]
    )
    member = store.begin_member(created.id, "round")
    read_boundary = len(store.messages(created.id))
    store.append(created.id, "user", "arrived while answering", speaker="user")
    store.append(
        created.id,
        "assistant",
        "the numbers say yes",
        speaker=member.id,
        turn_id="round",
    )
    store.finish_member(created.id, "round", read_boundary=read_boundary)
    assert cursor_for(store, created.id, member.id) == read_boundary == 1
    assert cursor_for(store, created.id, "skeptic") == 0
    feed, sliced = member_feed(
        store.messages(created.id), read_boundary, member.id, remembers=True
    )
    assert sliced and [row["content"] for row in feed] == ["arrived while answering"]
    assert cursors_path(store, created.id).stat().st_mode & 0o777 == 0o600
    full, sliced = member_feed(
        store.messages(created.id), read_boundary, member.id, remembers=False
    )
    assert not sliced and any(row["content"] == "the numbers say yes" for row in full)


def test_failed_and_interrupted_answers_do_not_acknowledge_context(home):
    store = RoomStore(home)
    created = room(store)
    store.append(created.id, "user", "before", speaker="user")
    advance(store, created.id, "analyst", 1)
    store.begin_turn(created.id, "what about cost?", "round", pending_queue=["analyst"])
    store.begin_member(created.id, "round")
    before = member_feed(store.messages(created.id), 1, "analyst", remembers=True)[0]
    store.append(
        created.id,
        "assistant",
        "unfinished",
        speaker="analyst",
        turn_id="round",
        interrupted=True,
    )
    with pytest.raises(ValueError, match="stored reply"):
        store.finish_member(created.id, "round", read_boundary=2)
    store.fail_member(created.id, "round", "member budget exceeded")
    assert cursor_for(store, created.id, "analyst") == 1
    after = member_feed(store.messages(created.id), 1, "analyst", remembers=True)[0]
    assert after[: len(before)] == before
    assert "what about cost?" in MemberTurn(created, created.members[0]).prompt(after)


@pytest.mark.parametrize(
    "raw", ["{not json", "[]", '{"analyst":true}', '{"analyst":-1}', '{"analyst":1.5}']
)
def test_corrupt_cursor_refuses_actual_round_before_writing_or_opening_session(
    home, raw
):
    store = RoomStore(home)
    created = room(store)
    path = cursors_path(store, created.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw)
    runtime = RoomTurns(
        store, ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    )
    with pytest.raises(RoomCursorError, match="round was refused"):
        runtime.submit(created.id, "Review", "round")
    assert store.turn(created.id) is None
    assert store.messages(created.id) == []
    assert path.read_text() == raw
    with pytest.raises(RoomCursorError):
        advance(store, created.id, "analyst", 0)
    assert path.read_text() == raw


@pytest.mark.asyncio
async def test_cursor_corruption_after_checkpoint_becomes_visible_member_failure(home):
    store = RoomStore(home)
    created = room(store)
    store.begin_turn(
        created.id, "Review", "round", pending_queue=["analyst", "skeptic"]
    )
    path = cursors_path(store, created.id)
    path.write_text('{"analyst":true}')
    runtime = RoomTurns(
        store, ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    )
    lock = store.acquire_turn(created.id)
    record = store.turn(created.id)
    runtime._launch(created, "Review", record, lock)
    await runtime._tasks[created.id]
    await asyncio.sleep(0)
    assert store.turn(created.id)["status"] == "failed"
    assert store.get(created.id).owed() == ["analyst", "skeptic"]
    assert any(
        row["role"] == "system" and "member context cursors" in row["content"]
        for row in store.messages(created.id)
    )
    assert not any(row["role"] == "assistant" for row in store.messages(created.id))


def test_atomic_process_updates_keep_both_members_and_tenant_scope(home):
    store = RoomStore(home)
    created = room(store)
    for text in ("one", "two"):
        store.append(created.id, "user", text, speaker="user")
    child = """
import os
from pathlib import Path
from gideon.engine.rooms.store import RoomStore
from gideon.engine.rooms.cursors import advance
advance(RoomStore(Path(os.environ['GIDEON_HOME'])), os.environ['ROOM_ID'], os.environ['MEMBER_ID'], int(os.environ['OFFSET']))
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", child],
            env=dict(
                os.environ, ROOM_ID=created.id, MEMBER_ID=name, OFFSET=str(offset)
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name, offset in (("analyst", 1), ("skeptic", 2))
    ]
    for process in children:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode()
    assert read_cursors(RoomStore(home), created.id) == {"analyst": 1, "skeptic": 2}
    with pytest.raises(KeyError):
        read_cursors(RoomStore(home / "another-owner"), created.id)


def test_removal_and_readd_preserve_cursor_and_full_context_is_not_silently_cut(home):
    store = RoomStore(home)
    created = room(store)
    for index in range(110):
        store.append(created.id, "user", f"position {index}", speaker="user")
    advance(store, created.id, "analyst", 1)
    store.update(created.id, members=[{"id": "skeptic", "agent": "default"}])
    store.update(
        created.id,
        members=[
            {"id": "analyst", "agent": "default"},
            {"id": "skeptic", "agent": "default"},
        ],
    )
    assert cursor_for(store, created.id, "analyst") == 1
    full, _ = member_feed(store.messages(created.id), 1, "analyst", remembers=False)
    prompt = MemberTurn(created, created.members[0]).prompt(full)
    assert "position 0\n" in prompt and "position 109" in prompt
    path = cursors_path(store, created.id)
    store.delete(created.id)
    assert not path.exists()
