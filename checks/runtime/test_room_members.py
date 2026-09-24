import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms import RoomMember, RoomStore
from gideon.engine.rooms.turn import RoomTurns, member_listens, plan_member_turns
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.rooms import setup_room_routes
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def room_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"rooms": {"enabled": True, "max_members": 8, "round_budget": 2}})
    )
    return tmp_path


def test_member_fields_survive_store_restart_and_legacy_rows(room_home):
    store = RoomStore(room_home)
    room = store.create(
        "Review",
        [
            {
                "id": "reviewer",
                "agent": "security-review",
                "name": "Security",
                "role": "Assess security risks",
                "listen_policy": "mentions",
            }
        ],
    )
    persisted = RoomStore(room_home).get(room.id).members[0]
    assert persisted == RoomMember(
        "reviewer", "security-review", "Security", "Assess security risks", "mentions"
    )
    document = json.loads(store.path.read_text())
    legacy = document["rooms"][0]["members"][0]
    del legacy["role"]
    del legacy["listen_policy"]
    store.path.write_text(json.dumps(document))
    loaded = RoomStore(room_home).get(room.id).members[0]
    assert loaded.agent == "security-review"
    assert loaded.role == ""
    assert loaded.listen_policy == "all"
    store.update(
        room.id,
        members=[
            {
                "id": "reviewer",
                "agent": "implementation",
                "role": "Write the change",
                "listen_policy": "none",
            }
        ],
    )
    changed = RoomStore(room_home).get(room.id).members[0]
    assert (changed.agent, changed.role, changed.listen_policy) == (
        "implementation",
        "Write the change",
        "none",
    )


@pytest.mark.parametrize(
    "policy,content,expected",
    [
        ("all", "ordinary message", True),
        ("none", "@reviewer please speak", False),
        ("mentions", "@reviewer please review", True),
        ("mentions", "@REVIEWER, please review", True),
        ("mentions", "Please @Security Expert review", True),
        ("mentions", "@reviewer-other", False),
        ("mentions", "@reviewers", False),
        ("mentions", "mail@reviewer", False),
        ("mentions", "@@reviewer", False),
        ("mentions", "ordinary message", False),
    ],
)
def test_listen_policy_mention_boundaries(policy, content, expected):
    member = RoomMember("reviewer", "security", "Security Expert", listen_policy=policy)
    assert member_listens(member, content) is expected


def test_turn_plan_consumes_binding_role_and_policy_before_budget(room_home):
    store = RoomStore(room_home)
    room = store.create(
        "Design",
        [
            {"id": "silent", "agent": "observe", "listen_policy": "none"},
            {
                "id": "reviewer",
                "agent": "review-model",
                "name": "Reviewer",
                "role": "Find defects",
                "listen_policy": "mentions",
            },
            {"id": "builder", "agent": "build-model", "role": "Propose fixes"},
        ],
    )
    plan = plan_member_turns(room, "@reviewer evaluate", 1)
    assert len(plan) == 1
    turn = plan[0]
    assert turn.key == f"room:{room.id}:reviewer"
    assert turn.provider_options == {
        "agent": "review-model",
        "approval_policy": "ask",
        "unattended": False,
    }
    prompt = turn.prompt(
        [
            {"role": "user", "speaker": "user", "content": "@reviewer evaluate"},
            {"role": "assistant", "speaker": "builder", "content": "A draft"},
        ]
    )
    assert "You are Reviewer" in prompt
    assert "Your role in this room: Find defects" in prompt
    assert "builder: A draft" in prompt
    assert "user: @reviewer evaluate" in prompt
    unmentioned = plan_member_turns(room, "ordinary message", 1)
    assert [item.member.id for item in unmentioned] == ["builder"]
    assert unmentioned[0].provider_options["agent"] == "build-model"
    assert "Propose fixes" in unmentioned[0].prompt([])


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", None),
        ("role", "x" * 4001),
        ("role", ["role"]),
        ("listen_policy", "auto"),
        ("listen_policy", None),
        ("listen_policy", ["all"]),
        ("agent", ""),
        ("agent", {}),
    ],
)
def test_invalid_member_fields_are_rejected_without_write(room_home, field, value):
    store = RoomStore(room_home)
    room = store.create("Valid", [{"id": "a", "agent": "default"}])
    before = store.path.read_bytes()
    member = {"id": "a", "agent": "default", field: value}
    with pytest.raises(ValueError):
        store.update(room.id, members=[member])
    assert store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_api_round_trip_member_fields_and_validation(room_home):
    app = web.Application()
    setup_room_routes(app, RoomStore(room_home))
    async with TestClient(TestServer(app)) as client:
        members = [
            {
                "id": "a",
                "agent": "writer",
                "name": "Author",
                "role": "Draft",
                "listen_policy": "all",
            }
        ]
        response = await client.post(
            "/api/rooms", json={"name": "Work", "members": members}
        )
        assert response.status == 201
        created = (await response.json())["room"]
        assert created["members"] == members
        base = "/api/rooms/" + created["id"]
        for policy in ("mentions", "none", "all"):
            members[0].update(
                agent="reviewer", role="Check the draft", listen_policy=policy
            )
            response = await client.patch(base, json={"members": members})
            assert response.status == 200
            assert (await response.json())["room"]["members"] == members
            response = await client.get(base)
            assert (await response.json())["room"]["members"] == members
        bad = {**members[0], "listen_policy": "invalid"}
        assert (await client.patch(base, json={"members": [bad]})).status == 400
        response = await client.get(base)
        assert (await response.json())["room"]["members"] == members


@pytest.mark.asyncio
async def test_real_turn_loop_skips_nonlisteners_and_preserves_message(room_home):
    store = RoomStore(room_home)
    room = store.create(
        "Quiet",
        [
            {"id": "observer", "agent": "observe", "listen_policy": "none"},
            {
                "id": "reviewer",
                "agent": "review",
                "role": "Check",
                "listen_policy": "mentions",
            },
        ],
    )
    sessions = ConversationDirectory(AppConfig.load())
    state = ConsoleState(sessions, time.time())
    turns = RoomTurns(store, state)
    messages = await turns.run(room.id, "A general note")
    assert len(messages) == 1
    assert messages[0]["content"] == "A general note"
    assert sessions.count == 0
    assert not turns.active(room.id)
    with pytest.raises(RuntimeError, match="No provider factory"):
        await turns.run(room.id, "@reviewer evaluate the note")
    assert not turns.active(room.id)
    assert len(store.messages(room.id)) == 2
