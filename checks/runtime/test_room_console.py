import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms import RoomMember, RoomStore
from gideon.engine.rooms.turn import RoomBusyError, RoomTurns, member_listens
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.rooms import setup_room_routes
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def room_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_AGENT_ID", raising=False)
    monkeypatch.delenv("GIDEON_SESSION_KEY", raising=False)
    (tmp_path / "config.json").write_text('{"rooms":{"enabled":true}}')
    return tmp_path


def state():
    return ConsoleState(ConversationDirectory(AppConfig.load()), time.time())


def test_transcript_snapshots_paging_and_legacy_recovery(room_home):
    store = RoomStore(room_home)
    room = store.create("Review", [{"id": "a", "agent": "default", "name": "Analyst"}])
    first = store.append(room.id, "user", "Review this", speaker="user")
    answer = store.append(room.id, "assistant", "Reviewed", speaker="a")
    store.update(room.id, members=[])
    last = store.append(room.id, "user", "Next", speaker="user")
    reopened = RoomStore(room_home)
    rows = reopened.messages(room.id, limit=2)
    assert [row["id"] for row in rows] == [answer["id"], last["id"]]
    assert rows[0]["speaker_name"] == "Analyst"
    assert rows[0]["created_at"] == rows[0]["ts"]
    assert reopened.messages(room.id, limit=2, before=answer["id"]) == [first]
    with pytest.raises(ValueError, match="cursor"):
        reopened.messages(room.id, before="missing")
    path = store.transcript._path(room.id)
    with path.open("a") as stream:
        stream.write(
            '{"role":"user","content":"Legacy","speaker":"user","ts":"2026-01-01"}\n'
        )
        stream.write('{"partial":')
    legacy = reopened.messages(room.id)[-1]
    assert legacy["id"] == RoomStore(room_home).messages(room.id)[-1]["id"]
    store.append(room.id, "user", "After interrupted write", speaker="user")
    assert len(store.messages(room.id)) == 5


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@everyone review", True),
        ("@EVERYONE!", True),
        ("email@everyone", False),
        ("-@everyone", False),
        ("@everyone-else", False),
        ("@équipe, review", True),
        ("@e\u0301quipe review", True),
        ("@équipe日", False),
        ("@équipe\u0308", False),
        ("@équipement", False),
    ],
)
def test_unicode_mentions_and_everyone(text, expected):
    member = RoomMember("a", "default", "équipe", listen_policy="mentions")
    assert member_listens(member, text) is expected
    member.listen_policy = "none"
    assert not member_listens(member, text)


@pytest.mark.asyncio
async def test_durable_deduplication_and_queued_cancellation(room_home):
    store = RoomStore(room_home)
    room = store.create(
        "Quiet", [{"id": "a", "agent": "default", "listen_policy": "none"}]
    )
    turns = RoomTurns(store, state())
    queued = turns.submit(room.id, "A note", "request-1")
    assert queued["status"] == "queued"
    assert turns.submit(room.id, "A note", "request-1") == queued
    with pytest.raises(RoomBusyError):
        turns.submit(room.id, "Changed text", "request-1")
    with pytest.raises(RoomBusyError):
        turns.submit(room.id, "Another request", "request-2")
    assert len(store.messages(room.id)) == 1
    cancelled = await turns.cancel(room.id)
    assert cancelled["status"] == "cancelled"
    assert not turns.active(room.id)
    reopened = RoomTurns(RoomStore(room_home), state())
    assert reopened.submit(room.id, "A note", "request-1")["status"] == "cancelled"
    assert len(store.messages(room.id)) == 1
    await reopened.run(room.id, "A second note")
    assert store.turn(room.id)["status"] == "completed"
    assert len(store.messages(room.id)) == 2


def test_restart_recovers_interrupted_turn_but_respects_live_owner(room_home):
    store = RoomStore(room_home)
    room = store.create("Quiet", [{"id": "a", "agent": "default"}])
    lock = store.acquire_turn(room.id)
    try:
        store.begin_turn(room.id, "Persisted", "request-1")
        RoomStore(room_home).recover_interrupted()
        assert store.turn(room.id)["status"] == "queued"
        with pytest.raises(RoomBusyError):
            RoomStore(room_home).acquire_turn(room.id)
        with pytest.raises(RoomBusyError):
            RoomStore(room_home).update(room.id, members=[])
        with pytest.raises(RoomBusyError):
            RoomStore(room_home).delete(room.id)
    finally:
        lock.close()
    reopened = RoomStore(room_home)
    reopened.recover_interrupted()
    assert reopened.turn(room.id)["status"] == "failed"
    assert "restart" in reopened.turn(room.id)["error"]
    assert len(reopened.messages(room.id)) == 1


@pytest.mark.asyncio
async def test_real_runtime_failure_is_durable_and_unlocks_room(room_home):
    store = RoomStore(room_home)
    room = store.create("Runtime", [{"id": "a", "agent": "default"}])
    turns = RoomTurns(store, state())
    with pytest.raises(RuntimeError, match="No provider factory"):
        await turns.run(room.id, "Run")
    current = store.turn(room.id)
    assert current["status"] == "failed"
    assert "No provider factory" in current["error"]
    assert current["member_id"] == "a"
    assert not turns.active(room.id)
    assert RoomStore(room_home).turn(room.id) == current


@pytest.mark.asyncio
async def test_http_enable_transcript_turns_disconnect_and_validation(room_home):
    path = room_home / "config.json"
    path.write_text(
        json.dumps(
            {
                "rooms": {"enabled": False, "round_budget": 2},
                "unrelated": {"keep": "value"},
            }
        )
    )
    app = web.Application()
    app["state"] = state()
    store = RoomStore(room_home)
    setup_room_routes(app, store)
    async with TestClient(TestServer(app)) as client:
        disabled = await (await client.get("/api/rooms")).json()
        assert disabled["enabled"] is False and disabled["rooms"] == []
        assert (
            await client.post("/api/rooms/enable", json={"round_budget": 100})
        ).status == 400
        assert (await client.post("/api/rooms/enable", json={})).status == 200
        config = json.loads(path.read_text())
        assert config["unrelated"] == {"keep": "value"}
        assert config["rooms"]["round_budget"] == 2
        response = await client.post(
            "/api/rooms",
            json={
                "name": "Notes",
                "members": [{"id": "a", "agent": "default", "listen_policy": "none"}],
            },
        )
        room = (await response.json())["room"]
        base = "/api/rooms/" + room["id"]
        for invalid in (
            [],
            None,
            {"text": ""},
            {"text": "x", "request_id": None},
            {"text": "x" * 16001, "request_id": "x"},
        ):
            assert (await client.post(base + "/turns", json=invalid)).status == 400
        response = await client.post(
            base + "/turns", json={"text": "Remember this", "request_id": "request-1"}
        )
        assert response.status == 202
        response.close()
        for _ in range(20):
            turn = (await (await client.get(base + "/turn")).json())["turn"]
            if turn["status"] == "completed":
                break
            await asyncio.sleep(0)
        assert turn["status"] == "completed"
        assert "content_hash" not in turn
        retry = await client.post(
            base + "/turns", json={"text": "Remember this", "request_id": "request-1"}
        )
        assert (await retry.json())["turn"]["id"] == "request-1"
        assert len(store.messages(room["id"])) == 1
        for index in range(4):
            store.append(room["id"], "user", f"Note {index}", speaker="user")
        page = await (await client.get(base + "/transcript?limit=2")).json()
        assert page["has_more"] and len(page["messages"]) == 2
        previous = await (
            await client.get(
                base + "/transcript", params={"before": page["before"], "limit": 2}
            )
        ).json()
        assert previous["messages"][-1]["content"] == "Note 1"
        assert not {row["id"] for row in page["messages"]} & {
            row["id"] for row in previous["messages"]
        }
        assert (await client.get(base + "/transcript?limit=501")).status == 400
        assert (await client.get(base + "/transcript?before=missing")).status == 400
        assert (await client.post(base + "/cancel")).status == 200
        assert (await client.delete(base)).status == 200
        assert not (store.directory / "turns" / room["id"]).exists()
