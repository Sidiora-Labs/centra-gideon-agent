import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.interfaces.dashboard.handlers.capabilities_identity_goals import register, PREFIX


def goal(store, **changes):
    return store.save_goal(**{"title": "Learn astronomy", "description": "Understand the sky", "target_date": "2027-01-01", "request_id": "goal", **changes})


def session(store, g, **changes):
    return store.save_session(**{"goal_id": g["id"], "title": "Observe stars", "start_at": "2026-10-01T21:00:00+02:00", "end_at": "2026-10-01T22:00:00+02:00", "request_id": "session", **changes})


def test_goal_and_session_persistence_timezone_and_replay(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    g = goal(store)
    s = session(store, g)
    assert g["revision"] == 1
    assert g["status"] == "active"
    assert g["target_date"] == "2027-01-01"
    assert s["start_at"] == "2026-10-01T19:00:00.000000+00:00"
    assert s["end_at"] == "2026-10-01T20:00:00.000000+00:00"
    assert s["goal_id"] == g["id"]
    assert s["status"] == "scheduled"
    assert datetime.fromisoformat(g["created_at"]).tzinfo == timezone.utc
    reopened = GoalStore(store.path)
    assert reopened.get_goal(g["id"]) == g
    assert reopened.get_session(s["id"]) == s
    assert reopened.list_goals() == [g]
    assert reopened.list_sessions() == [s]
    assert goal(reopened) == g
    assert session(reopened, g) == s
    with pytest.raises(ConflictError, match="different planning"):
        goal(reopened, title="Other")
    with pytest.raises(ConflictError, match="different planning"):
        session(reopened, g, title="Other")
    assert reopened.list_sessions() == [s]


def test_revisions_replay_and_stale_mutation(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    original = goal(store)
    changed = goal(store, id=original["id"], expected_revision=1, request_id="edit", description="New reason")
    assert changed["revision"] == 2
    assert changed["description"] == "New reason"
    assert changed["created_at"] == original["created_at"]
    assert changed["updated_at"] >= original["updated_at"]
    assert goal(store, id=original["id"], expected_revision=1, request_id="edit", description="New reason") == changed
    with pytest.raises(ConflictError, match="reload"):
        goal(store, id=original["id"], expected_revision=1, request_id="stale")
    assert store.get_goal(original["id"]) == changed
    assert goal(store) == original
    assert store.get_goal(original["id"]) == changed


def test_overlap_is_global_atomic_and_adjacent_allowed(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    first = goal(store)
    second = goal(store, title="Exercise", request_id="second")
    original = session(store, first)
    with pytest.raises(ConflictError, match="overlaps"):
        session(store, second, request_id="overlap", start_at="2026-10-01T19:30:00Z", end_at="2026-10-01T21:00:00Z")
    adjacent = session(store, second, request_id="adjacent", start_at="2026-10-01T20:00:00Z", end_at="2026-10-01T21:00:00Z")
    assert store.list_sessions() == [original, adjacent]
    same = session(store, first, id=original["id"], expected_revision=1, request_id="rename", title="Observe Jupiter")
    assert same["revision"] == 2
    assert same["title"] == "Observe Jupiter"
    with pytest.raises(ConflictError, match="reload"):
        session(store, first, id=original["id"], expected_revision=1, request_id="stale")


def test_resolve_sessions_before_goal_completion_and_reopen(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    g = goal(store)
    s = session(store, g)
    with pytest.raises(ConflictError, match="Resolve"):
        goal(store, id=g["id"], expected_revision=1, status="completed", request_id="complete")
    resolved = session(store, g, id=s["id"], expected_revision=1, status="completed", request_id="resolve")
    assert resolved["status"] == "completed"
    closed = goal(store, id=g["id"], expected_revision=1, status="completed", request_id="complete")
    assert closed["status"] == "completed"
    with pytest.raises(ConflictError, match="active goals"):
        session(store, g, request_id="again")
    reopened = goal(store, id=g["id"], expected_revision=2, request_id="reopen")
    assert reopened["status"] == "active"
    scheduled = session(store, g, request_id="again")
    assert scheduled["status"] == "scheduled"
    cancelled = session(store, g, id=scheduled["id"], expected_revision=1, status="cancelled", request_id="cancel")
    assert cancelled["status"] == "cancelled"
    archived = goal(store, id=g["id"], expected_revision=3, status="archived", request_id="archive")
    assert archived["revision"] == 4


@pytest.mark.parametrize("changes", [
    {"title": ""}, {"title": "x" * 201}, {"description": None}, {"status": "running"},
    {"target_date": "2026-02-30"}, {"target_date": "20261001"}, {"expected_revision": True},
    {"request_id": ""}, {"id": ""},
])
def test_goal_validation_no_writes(changes, tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    with pytest.raises((ValueError, TypeError)):
        goal(store, **changes)
    assert store.list_goals() == []


@pytest.mark.parametrize("changes", [
    {"start_at": "2026-10-01T21:00:00"}, {"end_at": None}, {"start_at": "invalid"},
    {"end_at": "2026-10-01T20:00:00+02:00"}, {"end_at": "2026-10-01T21:00:00+02:00"},
    {"end_at": "2026-10-03T22:00:00+02:00"}, {"status": "active"}, {"notes": "x" * 10001},
])
def test_session_validation_no_writes(changes, tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    g = goal(store)
    with pytest.raises((ValueError, TypeError)):
        session(store, g, **changes)
    assert store.list_sessions() == []


def test_cross_home_reference_and_isolation(tmp_path):
    first = GoalStore(tmp_path / "one/goals.sqlite3")
    second = GoalStore(tmp_path / "two/goals.sqlite3")
    g = goal(first)
    with pytest.raises(KeyError):
        session(second, g)
    assert second.list_goals() == []
    assert second.list_sessions() == []
    with pytest.raises(KeyError):
        second.get_goal(g["id"])
    assert first.get_goal(g["id"]) == g


def test_concurrent_overlap_claim_only_one_winner(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    g = goal(store)
    def claim(index):
        try:
            return session(GoalStore(store.path), g, request_id=str(index))
        except ConflictError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(claim, range(4)))
    assert sum(row is not None for row in rows) == 1
    assert len(store.list_sessions()) == 1
    assert store.list_sessions()[0] in rows


def test_calendar_escaping_folding_timezone_cancellation_and_stable_uid(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite3")
    g = goal(store)
    s = session(store, g, title="Stars; moon, sky", notes="Line one\nBEGIN:VEVENT\n" + "星" * 90)
    calendar = store.calendar()
    assert calendar.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert calendar.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261001T190000Z\r\n" in calendar
    assert "DTEND:20261001T200000Z\r\n" in calendar
    assert f"UID:{s['id']}@gideon.local\r\n" in calendar
    assert f"RELATED-TO:{g['id']}@gideon.local\r\n" in calendar
    assert "SUMMARY:Stars\\; moon\\, sky" in calendar
    assert "DESCRIPTION:Line one\\nBEGIN:VEVENT\\n" in calendar
    assert calendar.count("\r\nBEGIN:VEVENT\r\n") == 1
    assert all(len(line.encode()) <= 75 for line in calendar.split("\r\n"))
    assert "星" * 90 in calendar.replace("\r\n ", "")
    cancelled = session(store, g, id=s["id"], expected_revision=1, request_id="cancel", status="cancelled")
    assert cancelled["revision"] == 2
    updated = store.calendar()
    assert "STATUS:CANCELLED\r\n" in updated
    assert "SEQUENCE:2\r\n" in updated
    assert f"UID:{s['id']}@gideon.local\r\n" in updated


@pytest.mark.asyncio
async def test_actual_http_lifecycle_conflicts_calendar_and_errors(tmp_path):
    app = web.Application()
    register(app, store_path=tmp_path / "goals.sqlite3")
    async with TestClient(TestServer(app)) as client:
        assert await (await client.get(PREFIX + "/goals")).json() == []
        body = {"title": "Fitness", "request_id": "goal"}
        response = await client.post(PREFIX + "/goals", json=body)
        assert response.status == 200
        g = await response.json()
        assert await (await client.post(PREFIX + "/goals", json=body)).json() == g
        assert await (await client.get(PREFIX + "/goals/" + g["id"])).json() == g
        payload = {"goal_id": g["id"], "title": "Walk", "start_at": "2026-10-02T10:00:00Z", "end_at": "2026-10-02T11:00:00Z", "request_id": "session"}
        response = await client.post(PREFIX + "/sessions", json=payload)
        assert response.status == 200
        s = await response.json()
        assert await (await client.get(PREFIX + "/sessions/" + s["id"])).json() == s
        conflict = await client.post(PREFIX + "/sessions", json={**payload, "request_id": "collision"})
        assert conflict.status == 409
        assert "overlaps" in (await conflict.json())["error"]
        calendar = await client.get(PREFIX + "/calendar")
        assert calendar.status == 200
        assert calendar.content_type == "text/calendar"
        assert "attachment" in calendar.headers["Content-Disposition"]
        assert "SUMMARY:Walk" in await calendar.text()
        assert (await client.get(PREFIX + "/goals/missing")).status == 404
        assert (await client.post(PREFIX + "/goals", json=[])).status == 400
        assert (await client.post(PREFIX + "/goals", json={**body, "home": "/tmp"})).status == 400
        assert await (await client.get(PREFIX + "/sessions")).json() == [s]


@pytest.mark.asyncio
async def test_actual_native_planning_operations_and_session_boundary(tmp_path):
    provider = IdentityToolProvider(tmp_path)
    token = set_current_session_key("dashboard:human-plans")
    try:
        result = await provider.invoke("identity_goals_save_goal", {"title": "Garden", "request_id": "goal"})
        assert result.success
        g = json.loads(result.output)
        result = await provider.invoke("identity_goals_get_goal", {"id": g["id"]})
        assert json.loads(result.output) == g
        result = await provider.invoke("identity_goals_list_goals", {})
        assert json.loads(result.output) == [g]
        result = await provider.invoke("identity_goals_save_session", {"goal_id": g["id"], "title": "Plant", "start_at": "2026-10-01T10:00:00Z", "end_at": "2026-10-01T11:00:00Z", "request_id": "session"})
        assert result.success
        s = json.loads(result.output)
        result = await provider.invoke("identity_goals_get_session", {"id": s["id"]})
        assert json.loads(result.output) == s
        result = await provider.invoke("identity_goals_list_sessions", {})
        assert json.loads(result.output) == [s]
        result = await provider.invoke("identity_goals_calendar", {})
        assert "SUMMARY:Plant" in json.loads(result.output)["calendar"]
        invalid = await provider.invoke("identity_goals_save_goal", {"title": "Other"})
        assert not invalid.success
        assert "request_id" in invalid.error
    finally:
        reset_current_session_key(token)
    token = set_current_session_key("telegram:outside")
    try:
        result = await provider.invoke("identity_goals_list_goals", {})
        assert not result.success
        assert "private conversation" in result.error
    finally:
        reset_current_session_key(token)
