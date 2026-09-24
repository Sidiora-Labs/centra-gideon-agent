import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config.decoding import decode_configuration
from gideon.core.config.loader import AppConfig
from gideon.engine.rooms import RoomStore, session_key
from gideon.engine.rooms.turn import RoomTurns
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard import session_export
from gideon.interfaces.dashboard.handlers.rooms import setup_room_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations.durability.inventory import audit_home, claim_for, export_entries
from gideon.security.guardrails.policy import is_unattended_session, profile_for_session
from gideon.security.security import redact_field


@pytest.fixture
def room_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"rooms": {"enabled": True, "round_budget": 2, "max_members": 2}})
    )
    return tmp_path


def test_durable_records_transcript_recovery_and_inventory(room_home):
    store = RoomStore(room_home)
    room = store.create("Design", [{"id": "planner", "agent": "default"}])
    assert (
        json.loads((room_home / "rooms/index.json").read_text())["rooms"][0]["members"][
            0
        ]["id"]
        == "planner"
    )
    store.append(room.id, "user", "Hello", speaker="user")
    store.append(room.id, "assistant", "A plan", speaker="planner")
    reopened = RoomStore(room_home)
    assert isinstance(reopened.transcript, ConversationLog)
    assert reopened.get(room.id) == room
    assert reopened.messages(room.id)[-1]["speaker"] == "planner"
    path = reopened.transcript._path(room.id)
    with path.open("a") as stream:
        stream.write('{"partial":')
    recovered = RoomStore(room_home)
    assert len(recovered.messages(room.id)) == 2
    assert recovered.messages(room.id)[-1]["content"] == "A plan"
    recovered.append(room.id, "user", "After recovery", speaker="user")
    assert len(RoomStore(room_home).messages(room.id)) == 3
    assert claim_for("rooms/index.json").id == "rooms"
    assert claim_for("rooms/transcripts/a.jsonl").id == "rooms"
    assert any(e.id == "rooms" for e in export_entries())
    assert audit_home(room_home).ok
    recovered.delete(room.id)
    assert recovered.list() == []
    assert not path.exists()


def test_validation_update_and_session_posture(room_home):
    store = RoomStore(room_home)
    room = store.create("One")
    assert (
        store.update(
            room.id, name="Two", members=[{"id": "a", "agent": "default"}]
        ).name
        == "Two"
    )
    key = session_key(room.id, "a")
    assert key == f"room:{room.id}:a"
    assert not is_unattended_session(key)
    profile = profile_for_session(key)
    assert profile.name == "interactive"
    assert profile.approval == "ask"
    for invalid in ("../escape", "a:b", "", "a/b"):
        with pytest.raises(ValueError):
            session_key(room.id, invalid)
    with pytest.raises(ValueError):
        store.create("bad", [{"id": "a", "agent": "default"}] * 2)
    with pytest.raises(ValueError):
        store.create("bad", [{"id": "a", "agent": "default"}], max_members=0)
    with pytest.raises(ValueError):
        store.append(room.id, "assistant", "wrong", speaker="unknown")
    with pytest.raises(ValueError):
        store.create("bad", [None])
    assert len(store.list()) == 1


def test_config_and_additive_history(room_home):
    config = AppConfig.load()
    assert (
        config.rooms.enabled
        and config.rooms.round_budget == 2
        and config.rooms.max_members == 2
    )
    assert AppConfig().rooms.enabled is False
    assert decode_configuration(config.to_dict(), AppConfig).rooms == config.rooms
    log = ConversationLog(room_home / "sessions")
    log.append("old", "user", "plain")
    assert "speaker" not in log.read_messages("old")[0]
    log.append("old", "assistant", "answer", speaker="member")
    assert log.read_messages("old")[1]["speaker"] == "member"
    assert session_export.redact_field is redact_field
    secret = "sk-" + "a" * 48
    assert secret not in redact_field(secret)
    export = json.loads(
        session_export.render_json(
            title=secret,
            key="room",
            meta={},
            messages=[{"role": "assistant", "content": secret, "speaker": secret}],
        )
    )
    assert secret not in json.dumps(export)


@pytest.mark.asyncio
async def test_eight_routes_and_live_kill_switch(room_home):
    app = web.Application()
    store = RoomStore(room_home)
    setup_room_routes(app, store)
    assert len(list(app.router.routes())) == 8
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/rooms", json={"name": "Room"})
        assert response.status == 201
        room = (await response.json())["room"]
        base = f"/api/rooms/{room['id']}"
        assert (await client.get("/api/rooms")).status == 200
        assert (await client.get(base)).status == 200
        response = await client.patch(
            base, json={"members": [{"id": "a", "agent": "default"}]}
        )
        assert response.status == 200
        store.append(room["id"], "assistant", "hello", speaker="a")
        transcript = await (await client.get(base + "/transcript")).json()
        assert transcript["messages"][0]["speaker"] == "a"
        exported = await (await client.get(base + "/export")).json()
        assert exported["messages"][0]["speaker"] == "a"
        md = await (await client.get(base + "/export?format=md")).text()
        assert "## a" in md
        assert (
            await client.post(base + "/messages", json={"content": "hello"})
        ).status == 503
        assert (await client.patch(base, json={"members": [None]})).status == 400
        assert (await client.patch(base, json={"name": None})).status == 400
        before = store.path.read_bytes()
        (room_home / "config.json").write_text('{"rooms":{"enabled":false}}')
        for method, path in [
            ("GET", "/api/rooms"),
            ("POST", "/api/rooms"),
            ("GET", base),
            ("PATCH", base),
            ("DELETE", base),
            ("GET", base + "/transcript"),
            ("GET", base + "/export"),
            ("POST", base + "/messages"),
        ]:
            assert (
                await client.request(method, path, json={"name": "blocked"})
            ).status == 404
        assert store.path.read_bytes() == before
        (room_home / "config.json").write_text('{"rooms":{"enabled":true}}')
        assert (await client.delete(base)).status == 200
        assert (await client.get(base)).status == 404


@pytest.mark.asyncio
async def test_real_session_failure_preserves_turn_and_releases_room(room_home):
    store = RoomStore(room_home)
    room = store.create("Room", [{"id": "a", "agent": "default"}])
    sessions = ConversationDirectory(AppConfig.load())
    state = ConsoleState(sessions, time.time())
    turns = RoomTurns(store, state)
    with pytest.raises(RuntimeError, match="No provider factory"):
        await turns.run(room.id, "question")
    assert not turns.active(room.id)
    assert store.messages(room.id)[0]["content"] == "question"
    empty = store.create("Empty")
    with pytest.raises(ValueError, match="at least one"):
        await turns.run(empty.id, "question")
    assert store.messages(empty.id) == []
    (room_home / "config.json").write_text('{"rooms":{"enabled":false}}')
    with pytest.raises(PermissionError):
        await turns.run(room.id, "blocked")
    assert len(store.messages(room.id)) == 1
