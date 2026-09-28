import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms.store import RoomBusyError, RoomStore
from gideon.engine.rooms.turn import RoomTurns
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.rooms import setup_room_routes
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_AGENT_ID", raising=False)
    monkeypatch.delenv("GIDEON_SESSION_KEY", raising=False)
    (tmp_path / "config.json").write_text('{"rooms":{"enabled":true}}')
    return tmp_path


def state():
    return ConsoleState(ConversationDirectory(AppConfig.load()), time.time())


def room(store):
    return store.create("Review", [{"id": name, "agent": "default", "name": name.title()}
                                   for name in ("analyst", "skeptic", "closer")])


def test_a_turn_moves_the_queue_head_into_speaking_on_disk(home):
    store = RoomStore(home)
    created = room(store)
    store.begin_turn(created.id, "Review", "round", pending_queue=["analyst", "skeptic", "analyst", "closer"])
    assert store.get(created.id).pending_queue == ["analyst", "skeptic", "closer"]
    assert store.begin_member(created.id, "round").id == "analyst"
    raw = next(row for row in json.loads(store.path.read_text())["rooms"] if row["id"] == created.id)
    assert (raw["speaking"], raw["pending_queue"]) == ("analyst", ["skeptic", "closer"])
    store.append(created.id, "assistant", "Reviewed", speaker="analyst", turn_id="round")
    store.finish_member(created.id, "round")
    assert (store.get(created.id).speaking, store.get(created.id).pending_queue) == ("", ["skeptic", "closer"])


def test_actual_process_exit_preserves_the_queue_and_paid_members_do_not_replay(home):
    store = RoomStore(home)
    created = room(store)
    child = """
import os
from pathlib import Path
from gideon.engine.rooms.store import RoomStore
s = RoomStore(Path(os.environ["GIDEON_HOME"]))
r = os.environ["ROOM_ID"]
s.begin_turn(r, "Review", "round", pending_queue=["analyst", "skeptic", "closer"])
s.begin_member(r, "round")
s.append(r, "assistant", "Reviewed", speaker="analyst", turn_id="round")
os._exit(13)
"""
    env = dict(os.environ, ROOM_ID=created.id)
    result = subprocess.run([sys.executable, "-c", child], env=env, capture_output=True, timeout=20)
    assert result.returncode == 13, result.stderr.decode()
    reopened = RoomStore(home)
    runtime = RoomTurns(reopened, state())
    assert reopened.turn(created.id)["status"] == "paused"
    assert runtime.active(created.id) is False
    assert reopened.get(created.id).owed() == ["skeptic", "closer"]
    assert reopened.begin_member(created.id, "round").id == "skeptic"
    reopened.append(created.id, "assistant", "Noted", speaker="skeptic", turn_id="round")
    reopened.finish_member(created.id, "round")
    assert reopened.begin_member(created.id, "round").id == "closer"
    reopened.append(created.id, "assistant", "Agreed", speaker="closer", turn_id="round")
    reopened.finish_member(created.id, "round")
    assert reopened.begin_member(created.id, "round") is None
    assert [row["speaker"] for row in reopened.messages(created.id) if row["role"] == "assistant"] == ["analyst", "skeptic", "closer"]
    assert sum(row["role"] == "user" for row in reopened.messages(created.id)) == 1


@pytest.mark.asyncio
async def test_real_disabled_runtime_failure_is_visible_and_recoverable(home):
    store = RoomStore(home)
    created = room(store)
    runtime = RoomTurns(store, state())
    lock = store.acquire_turn(created.id)
    record, _ = store.begin_turn(created.id, "Review", "round", pending_queue=["analyst", "skeptic"])
    (home / "config.json").write_text('{"rooms":{"enabled":false}}')
    runtime._launch(created, "Review", record, lock)
    await runtime._tasks[created.id]
    await asyncio.sleep(0)
    assert store.turn(created.id)["status"] == "failed"
    assert store.get(created.id).owed() == ["analyst", "skeptic"]
    rows = store.messages(created.id)
    assert not any(row["role"] == "assistant" for row in rows)
    assert any(row["role"] == "system" and "Analyst could not finish" in row["content"] for row in rows)
    assert runtime.active(created.id) is False
    assert store.begin_member(created.id, "round").id == "analyst"


@pytest.mark.asyncio
async def test_concurrent_sends_have_one_real_round_owner_and_tenant_home_isolation(home):
    store = RoomStore(home)
    created = store.create("Quiet", [{"id": "a", "agent": "default", "listen_policy": "none"}])
    runtime = RoomTurns(store, state())
    runtime.submit(created.id, "A note", "one")
    assert runtime.active(created.id)
    with pytest.raises(RoomBusyError):
        runtime.submit(created.id, "Another note", "two")
    with pytest.raises(RoomBusyError):
        RoomStore(home).acquire_turn(created.id)
    other_home = home / "other-tenant"
    other_home.mkdir()
    other = RoomStore(other_home)
    other.create("Other")
    with pytest.raises(KeyError):
        other.get(created.id)
    await runtime._tasks[created.id]
    await asyncio.sleep(0)
    assert runtime.active(created.id) is False
    assert store.turn(created.id)["status"] == "completed"


@pytest.mark.asyncio
async def test_actual_continue_route_starts_no_idle_round_and_refuses_client_queue(home):
    store = RoomStore(home)
    created = room(store)
    app = web.Application()
    app["state"] = state()
    setup_room_routes(app, store)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(f"/api/rooms/{created.id}/continue", json={})
        assert response.status == 200
        assert (await response.json())["turn"] is None
        response = await client.post(f"/api/rooms/{created.id}/continue", json={"pending_queue": ["analyst"]})
        assert response.status == 400
        response = await client.get(f"/api/rooms/{created.id}")
        body = await response.json()
        assert body["room"]["round_running"] is False
        assert body["room"]["owed"] == []
