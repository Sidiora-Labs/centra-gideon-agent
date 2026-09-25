import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.interfaces.dashboard.handlers.capabilities_identity_goal_plans import register, PREFIX


def setup(home):
    store = GoalPlanStore(home / "capabilities/identity/goals.sqlite3")
    parent = store.goals.save_goal(title="Learn astronomy", request_id="parent")
    child = store.goals.save_goal(title="Build telescope", request_id="child")
    return store, parent, child


def configure(store, goal, **changes):
    return store.configure(**{"goal_id": goal["id"], "parent_id": None, "horizon": "long_term", "milestones": [], "links": [], "unit": "pages", "target_value": 100, "expected_revision": 0, "request_id": "plan-" + goal["id"], **changes})


def actual_sources(home):
    script = '''import asyncio,json
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.automation.loop import store
from gideon.automation.loop.loop import Loop
async def main():
 task=await NativeTaskProvider().create_task(title="Study optics")
 task=await NativeTaskProvider().update_task(task.id,status="done")
 loop=store.create(Loop(id="",name="Optics research",kind="goal",task="Research optics"))
 print(json.dumps({"task":task.id,"loop":loop.id}))
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "GIDEON_HOME": str(home), "PYTHONPATH": str(Path(__file__).resolve().parents[4] / "runtime")}, capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_hierarchy_milestones_replay_and_canonical_goals_unchanged(tmp_path):
    store, parent, child = setup(tmp_path)
    assert store.get(child["id"])["plan"]["revision"] == 0
    assert store.get(child["id"])["velocity"] is None
    assert store.get(child["id"])["milestones_complete_ratio"] is None
    milestones = [{"id": "mirror", "title": "Polish mirror", "done": True, "target_date": "2026-11-01"}, {"id": "mount", "title": "Build mount", "done": False, "target_date": None}]
    plan = configure(store, child, parent_id=parent["id"], milestones=milestones)
    assert plan["revision"] == 1
    assert plan["parent_id"] == parent["id"]
    assert configure(store, child, parent_id=parent["id"], milestones=milestones) == plan
    assert store.get(parent["id"])["children"] == [child["id"]]
    projected = store.get(child["id"])
    assert projected["milestones_complete_ratio"] == 0.5
    assert projected["goal"] == child
    assert store.goals.get_goal(child["id"])["status"] == "active"
    assert GoalPlanStore(store.path).get(child["id"]) == projected
    assert len(store.list()) == 2
    changed = configure(store, child, expected_revision=1, request_id="detach")
    assert changed["parent_id"] is None
    assert store.get(parent["id"])["children"] == []
    with pytest.raises(ConflictError, match="reload"):
        configure(store, child, request_id="stale")
    assert store.get(child["id"])["plan"] == changed


def test_atomic_cycle_and_foreign_parent_refusal(tmp_path):
    store, parent, child = setup(tmp_path)
    configure(store, child, parent_id=parent["id"])
    with pytest.raises(ValueError, match="cycle"):
        configure(store, parent, parent_id=child["id"])
    with pytest.raises(ValueError, match="cycle"):
        configure(store, parent, parent_id=parent["id"])
    with pytest.raises(KeyError):
        configure(store, parent, parent_id="foreign")
    assert store.get(parent["id"])["plan"]["revision"] == 0
    assert store.get(child["id"])["plan"]["parent_id"] == parent["id"]
    assert store.get(parent["id"])["goal"]["status"] == "active"


def test_real_task_loop_and_session_links_projection_and_missing_source(tmp_path):
    store, parent, _ = setup(tmp_path)
    sources = actual_sources(tmp_path)
    session = store.goals.save_session(goal_id=parent["id"], title="Observatory visit", start_at="2026-10-01T10:00:00Z", end_at="2026-10-01T11:00:00Z", request_id="session")
    links = [{"kind": "task", "id": sources["task"]}, {"kind": "loop", "id": sources["loop"]}, {"kind": "session", "id": session["id"]}]
    saved = configure(store, parent, links=links)
    view = store.get(parent["id"])
    assert [row["availability"] for row in view["linked_sources"]] == ["available"] * 3
    assert view["linked_sources"][0]["title"] == "Study optics"
    assert view["linked_sources"][0]["status"] == "done"
    assert view["linked_sources"][1]["title"] == "Optics research"
    assert view["linked_sources"][1]["status"] == "ready"
    assert view["linked_sources"][1]["loop_kind"] == "goal"
    assert view["linked_sources"][2]["status"] == "scheduled"
    assert view["goal"]["status"] == "active"
    assert view["checkins"] == []
    assert view["velocity"] is None
    assert "provider" not in view["linked_sources"][1]
    (tmp_path / "tasks" / (sources["task"] + ".json")).unlink()
    assert store.get(parent["id"])["linked_sources"][0]["availability"] == "missing"
    assert configure(store, parent, links=links) == saved
    with pytest.raises(ValueError, match="available local"):
        configure(store, parent, links=links, expected_revision=1, request_id="new-source-check")
    other = GoalPlanStore(tmp_path / "other/capabilities/identity/goals.sqlite3")
    other_goal = other.goals.save_goal(title="Different", request_id="other")
    with pytest.raises(ValueError):
        configure(other, other_goal, links=[links[1]])


def test_source_escape_does_not_cross_runtime_boundary(tmp_path):
    store, goal, _ = setup(tmp_path / "destination")
    outside = tmp_path / "outside"
    sources = actual_sources(outside)
    loopdir = store.home / "loop"
    loopdir.mkdir()
    (loopdir / "loops.db").symlink_to(outside / "loop/loops.db")
    with pytest.raises(ValueError, match="available local"):
        configure(store, goal, links=[{"kind": "loop", "id": sources["loop"]}])
    assert store.get(goal["id"])["plan"]["links"] == []
    with pytest.raises(ValueError):
        configure(store, goal, links=[{"kind": "task", "id": "../../secret"}])
    with pytest.raises(ValueError):
        configure(store, goal, links=[{"kind": "loop", "id": "../../secret"}])
    assert store.get(goal["id"])["plan"]["revision"] == 0


def test_observations_normalize_sort_deduplicate_and_derive_real_delta(tmp_path):
    store, goal, _ = setup(tmp_path)
    configure(store, goal)
    later = store.checkin(goal_id=goal["id"], value=30, observed_at="2026-10-03T12:00:00+02:00", notes="Read chapters", request_id="later")
    assert later["source"] == "human_reported"
    assert later["unit"] == "pages"
    assert later["observed_at"] == "2026-10-03T10:00:00.000000+00:00"
    assert store.get(goal["id"])["velocity"] is None
    earlier = store.checkin(goal_id=goal["id"], value=10, observed_at="2026-10-01T10:00:00Z", notes="Starting point", request_id="earlier")
    assert store.checkin(goal_id=goal["id"], value=10, observed_at="2026-10-01T10:00:00+00:00", notes="Starting point", request_id="earlier") == earlier
    projection = store.get(goal["id"])
    assert projection["checkins"] == [earlier, later]
    assert projection["velocity"] == {"value_per_day": 10, "unit": "pages", "from": earlier["observed_at"], "to": later["observed_at"]}
    assert GoalPlanStore(store.path).get(goal["id"]) == projection
    assert projection["goal"]["status"] == "active"
    with pytest.raises(ConflictError, match="already exists"):
        store.checkin(goal_id=goal["id"], value=11, observed_at="2026-10-01T10:00:00Z", notes="Duplicate instant", request_id="collision")
    with pytest.raises(ConflictError, match="another goal"):
        store.checkin(goal_id=goal["id"], value=11, observed_at="2026-10-01T10:00:00Z", notes="Starting point", request_id="earlier")
    with pytest.raises(ConflictError, match="unit cannot change"):
        configure(store, goal, unit="hours", expected_revision=1, request_id="unit-change")
    assert store.get(goal["id"])["checkins"] == [earlier, later]


@pytest.mark.parametrize("value", [float('nan'), float('inf'), True, "ten", 1e13])
def test_invalid_metric_values_never_create_observations(tmp_path, value):
    store, goal, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        store.checkin(goal_id=goal["id"], value=value, observed_at="2026-10-01T10:00:00Z", notes="", request_id="bad")
    assert store.get(goal["id"])["checkins"] == []
    assert store.get(goal["id"])["velocity"] is None


def test_milestone_and_observation_validation(tmp_path):
    store, goal, _ = setup(tmp_path)
    milestone = {"id": "one", "title": "Read", "done": False, "target_date": None}
    for invalid in [[milestone, milestone], [{**milestone, "done": 1}], [{**milestone, "target_date": "bad"}], [{**milestone, "title": ""}]]:
        with pytest.raises(ValueError):
            configure(store, goal, milestones=invalid)
    with pytest.raises(ValueError):
        configure(store, goal, horizon="tomorrow")
    with pytest.raises(ValueError):
        store.checkin(goal_id=goal["id"], value=1, observed_at="2026-10-01T10:00:00", notes="", request_id="naive")
    with pytest.raises(KeyError):
        store.checkin(goal_id="other", value=1, observed_at="2026-10-01T10:00:00Z", notes="", request_id="foreign")
    assert store.get(goal["id"])["plan"]["revision"] == 0
    assert store.get(goal["id"])["checkins"] == []


@pytest.mark.asyncio
async def test_actual_http_hierarchy_checkins_and_conflicts(tmp_path):
    store, parent, child = setup(tmp_path)
    app = web.Application()
    register(app, home=tmp_path)
    body = dict(goal_id=child["id"], parent_id=parent["id"], horizon="short_term", milestones=[], links=[], unit="pages", target_value=10, expected_revision=0, request_id="http")
    async with TestClient(TestServer(app)) as client:
        assert len(await (await client.get(PREFIX)).json()) == 2
        response = await client.post(PREFIX + "/configure", json=body)
        assert response.status == 200
        plan = await response.json()
        assert plan["revision"] == 1
        assert (await (await client.get(PREFIX + "/" + parent["id"])).json())["children"] == [child["id"]]
        for request_id, value, observed in [("first", 2, "2026-10-01T00:00:00Z"), ("second", 8, "2026-10-03T00:00:00Z")]:
            response = await client.post(PREFIX + "/checkins", json=dict(goal_id=child["id"], value=value, observed_at=observed, notes="Reading", request_id=request_id))
            assert response.status == 200
            assert (await response.json())["source"] == "human_reported"
        result = await (await client.get(PREFIX + "/" + child["id"])).json()
        assert result["velocity"]["value_per_day"] == 3
        assert result["goal"]["status"] == "active"
        assert (await client.post(PREFIX + "/configure", json={**body, "request_id": "stale"})).status == 409
        assert (await client.post(PREFIX + "/configure", json={**body, "home": "/other"})).status == 400
        assert (await client.get(PREFIX + "/missing")).status == 404
    assert store.get(child["id"])["velocity"]["value_per_day"] == 3


@pytest.mark.asyncio
async def test_native_goal_planning_uses_real_authoritative_store(tmp_path):
    store, parent, child = setup(tmp_path)
    provider = IdentityToolProvider(tmp_path)
    token = set_current_session_key("dashboard:goal-plans")
    try:
        result = await provider.invoke("identity_goal_plan_configure", dict(goal_id=child["id"], parent_id=parent["id"], horizon="long_term", milestones=[], links=[], unit="pages", target_value=20, expected_revision=0, request_id="native"))
        assert result.success
        assert json.loads(result.output)["revision"] == 1
        result = await provider.invoke("identity_goal_plan_checkin", dict(goal_id=child["id"], value=4, observed_at="2026-10-01T00:00:00Z", notes="Chapter", request_id="observation"))
        assert result.success
        result = await provider.invoke("identity_goal_plan_get", {"goal_id": child["id"]})
        view = json.loads(result.output)
        assert view["checkins"][0]["value"] == 4
        assert view["velocity"] is None
        assert view == store.get(child["id"])
        result = await provider.invoke("identity_goal_plan_list", {})
        assert len(json.loads(result.output)) == 2
    finally:
        reset_current_session_key(token)
