import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.identity.progress import ProgressStore
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.store import StoryStore, ConflictError
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.interfaces.dashboard.handlers.capabilities_identity_progress import register, PREFIX


def store_for(home):
    return ProgressStore(home / "capabilities/identity/progress.sqlite3")


def configure(store, **changes):
    return store.configure(**{"birth_date": None, "timezone": "UTC", "tracked_task_ids": [], "expected_revision": 0, "request_id": "settings", **changes})


def test_empty_sheet_preserves_unknowns_and_does_not_create_source_stores(tmp_path):
    store = store_for(tmp_path)
    result = store.sheet("2026-10-01")
    assert result["profile"]["revision"] == 0
    assert result["age"] is None
    assert result["goals"] == {"active": 0, "completed": 0, "archived": 0}
    assert result["sessions_completed"] == []
    assert result["planned_completed_minutes"] == 0
    assert result["tasks_done"] == 0
    assert result["unknown_metrics"] == ["health", "skill_level", "measured_effort"]
    assert result["authored_story_count"] == 0
    assert result["as_of"] == "2026-10-01"
    assert not (store.path.parent / "goals.sqlite3").exists()
    assert not (store.path.parent / "stories.sqlite3").exists()
    assert not (tmp_path / "tasks").exists()


@pytest.mark.parametrize("birthday,as_of,age", [
    ("2000-10-02", "2026-10-01", 25), ("2000-10-02", "2026-10-02", 26),
    ("2000-02-29", "2025-02-28", 24), ("2000-02-29", "2025-03-01", 25),
    (None, "2026-10-01", None), ("2000-01-01", "1999-12-31", None),
])
def test_age_derivation_and_unknown_before_birth(tmp_path, birthday, as_of, age):
    store = store_for(tmp_path)
    profile = configure(store, birth_date=birthday, timezone="Europe/Berlin")
    assert store.sheet(as_of)["age"] == age
    assert store.sheet(as_of)["profile"] == profile
    reopened = store_for(tmp_path)
    assert reopened.profile() == profile
    assert reopened.sheet(as_of)["age"] == age


def test_profile_replay_optimistic_edit_and_retry_conflicts(tmp_path):
    store = store_for(tmp_path)
    first = configure(store)
    assert configure(store) == first
    second = configure(store, timezone="Asia/Tokyo", expected_revision=1, request_id="edit")
    assert second["revision"] == 2
    assert second["timezone"] == "Asia/Tokyo"
    with pytest.raises(ConflictError, match="reload"):
        configure(store, expected_revision=1, request_id="stale")
    with pytest.raises(ConflictError, match="different progress"):
        configure(store, timezone="Europe/Berlin")
    assert configure(store) == first
    assert store.profile() == second
    assert store_for(tmp_path).profile() == second


@pytest.mark.parametrize("changes", [
    {"birth_date": "2030-01-01"}, {"birth_date": "2000-02-30"}, {"birth_date": "20000101"},
    {"timezone": "Not/AZone"}, {"tracked_task_ids": ["../escape"]}, {"tracked_task_ids": ["same", "same"]},
    {"tracked_task_ids": [False]}, {"expected_revision": True}, {"request_id": ""},
])
def test_validation_keeps_profile_unchanged(tmp_path, changes):
    store = store_for(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        configure(store, **changes)
    assert store.profile()["revision"] == 0
    assert store.profile()["tracked_task_ids"] == []


def test_session_projection_tracks_current_sources_and_local_date_without_duplicates(tmp_path):
    store = store_for(tmp_path)
    configure(store, timezone="America/New_York")
    goals = GoalStore(store.path.parent / "goals.sqlite3")
    goal = goals.save_goal(title="Read", request_id="goal")
    planned = goals.save_session(goal_id=goal["id"], title="Read book", start_at="2026-10-02T00:00:00Z", end_at="2026-10-02T01:00:00Z", request_id="session")
    assert store.sheet("2026-10-01")["sessions_completed"] == []
    completed = goals.save_session(goal_id=goal["id"], title=planned["title"], start_at=planned["start_at"], end_at=planned["end_at"], status="completed", id=planned["id"], expected_revision=1, request_id="complete")
    result = store.sheet("2026-10-01")
    assert result["sessions_completed"] == [{"id": completed["id"], "goal_id": goal["id"], "title": "Read book", "local_date": "2026-10-01", "planned_minutes": 60}]
    assert result["planned_completed_minutes"] == 60
    assert result["goals"]["active"] == 1
    assert store.sheet("2026-09-30")["sessions_completed"] == []
    assert store_for(tmp_path).sheet("2026-10-01") == result
    assert store.sheet("2026-10-01") == result
    goals.save_session(goal_id=goal["id"], title=planned["title"], start_at=planned["start_at"], end_at=planned["end_at"], status="cancelled", id=planned["id"], expected_revision=2, request_id="cancel")
    assert store.sheet("2026-10-01")["planned_completed_minutes"] == 0
    goals.save_goal(title="Read", id=goal["id"], expected_revision=1, request_id="close", status="completed")
    assert store.sheet("2026-10-01")["goals"]["completed"] == 1
    stories = StoryStore(store.path.parent / "stories.sqlite3")
    story = stories.create(prompt="Learning", theme="reading", text="I learned astronomy", request_id="story")
    assert store.sheet("2026-10-01")["authored_story_count"] == 1
    stories.update(story["id"], prompt="Learning", theme="reading", text="I learned optics", expected_revision=1)
    assert store.sheet("2026-10-01")["authored_story_count"] == 1
    assert "not historical" in store.sheet("2026-10-01")["source_policy"]


def test_real_native_task_provider_contributes_once_across_reads_and_status_changes(tmp_path):
    script = '''import asyncio,json,sys
from gideon.engine.tasks.native import NativeTaskProvider
async def main():
 p=NativeTaskProvider()
 if len(sys.argv)>1:
  row=await p.update_task(sys.argv[1],status="open")
 else:
  row=await p.create_task(title="Study optics")
  row=await p.update_task(row.id,status="done")
 print(json.dumps(row.to_dict()))
asyncio.run(main())
'''
    environment = {**os.environ, "GIDEON_HOME": str(tmp_path), "PYTHONPATH": str(Path(__file__).resolve().parents[4] / "runtime")}
    created = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True, check=True)
    task = json.loads(created.stdout.strip().splitlines()[-1])
    store = store_for(tmp_path)
    configure(store, tracked_task_ids=[task["id"]])
    sheet = store.sheet("2026-10-01")
    assert sheet["tasks"] == [{"id": task["id"], "title": "Study optics", "status": "done"}]
    assert sheet["tasks_done"] == 1
    assert store_for(tmp_path).sheet("2026-10-01")["tasks_done"] == 1
    assert store.sheet("2026-10-01")["tasks_done"] == 1
    subprocess.run([sys.executable, "-c", script, task["id"]], env=environment, capture_output=True, text=True, check=True)
    assert store.sheet("2026-10-01")["tasks_done"] == 0
    assert store.sheet("2026-10-01")["tasks"][0]["status"] == "open"
    (tmp_path / "tasks" / (task["id"] + ".json")).unlink()
    assert store.sheet("2026-10-01")["missing_task_ids"] == [task["id"]]
    assert store.sheet("2026-10-01")["tasks"] == []


def test_bad_or_escaped_source_does_not_become_successful_progress(tmp_path):
    store = store_for(tmp_path)
    configure(store, tracked_task_ids=["broken", "absent"])
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "broken.json").write_text("not json")
    result = store.sheet("2026-10-01")
    assert result["invalid_task_ids"] == ["broken"]
    assert result["missing_task_ids"] == ["absent"]
    assert result["tasks_done"] == 0
    (tasks / "broken.json").unlink()
    outside = tmp_path / "secret.json"
    outside.write_text('{"id":"broken","title":"secret","status":"done"}')
    (tasks / "broken.json").symlink_to(outside)
    result = store.sheet("2026-10-01")
    assert result["invalid_task_ids"] == ["broken"]
    assert "secret" not in json.dumps(result)
    assert result["tasks"] == []


@pytest.mark.asyncio
async def test_http_profile_projection_errors_and_isolated_homes(tmp_path):
    app = web.Application()
    register(app, store_path=tmp_path / "capabilities/identity/progress.sqlite3")
    async with TestClient(TestServer(app)) as client:
        response = await client.get(PREFIX + "?as_of=2026-10-01")
        assert response.status == 200
        assert (await response.json())["age"] is None
        body = dict(birth_date="2000-01-01", timezone="UTC", tracked_task_ids=[], expected_revision=0, request_id="profile")
        profile = await (await client.put(PREFIX, json=body)).json()
        assert profile["revision"] == 1
        assert await (await client.put(PREFIX, json=body)).json() == profile
        result = await (await client.get(PREFIX + "?as_of=2026-10-01")).json()
        assert result["age"] == 26
        assert result["profile"] == profile
        assert (await client.put(PREFIX, json={**body, "request_id": "stale"})).status == 409
        assert (await client.put(PREFIX, json={**body, "home": "other"})).status == 400
        assert (await client.get(PREFIX + "?as_of=bad")).status == 400
    other = store_for(tmp_path / "other")
    assert other.sheet("2026-10-01")["age"] is None
    assert other.profile()["revision"] == 0


@pytest.mark.asyncio
async def test_native_progress_configuration_and_read_actual_core(tmp_path):
    provider = IdentityToolProvider(tmp_path)
    token = set_current_session_key("dashboard:progress")
    try:
        response = await provider.invoke("identity_progress_sheet", {"as_of": "2026-10-01"})
        assert response.success
        assert json.loads(response.output)["age"] is None
        response = await provider.invoke("identity_progress_configure", dict(birth_date="2000-01-01", timezone="UTC", tracked_task_ids=[], expected_revision=0, request_id="native"))
        assert response.success
        assert json.loads(response.output)["revision"] == 1
        response = await provider.invoke("identity_progress_sheet", {"as_of": "2026-10-01"})
        assert json.loads(response.output)["age"] == 26
        assert store_for(tmp_path).profile()["birth_date"] == "2000-01-01"
        invalid = await provider.invoke("identity_progress_sheet", {"home": str(tmp_path)})
        assert not invalid.success
    finally:
        reset_current_session_key(token)
