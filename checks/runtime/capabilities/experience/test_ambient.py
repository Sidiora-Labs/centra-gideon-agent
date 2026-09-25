import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.ambient import AmbientDisplay
from gideon.workspace.capabilities.experience.store import Conflict
from gideon.workspace.capabilities.experience.tools import ExperienceTools
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.progress import ProgressStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementStore


@pytest.fixture
def ambient(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return AmbientDisplay(ExperienceStore(tmp_path))


def test_preferences_persist_revision_and_reject_stale_changes(ambient, tmp_path):
    first = ambient.preferences()
    assert first == {
        "revision": 1,
        "show_clock": True,
        "font_scale": 1,
        "idle_seconds": 30,
    }
    saved = ambient.save(
        {**first, "font_scale": 2, "show_clock": False, "idle_seconds": 60}
    )
    assert saved["revision"] == 2
    assert AmbientDisplay(ExperienceStore(tmp_path)).preferences() == saved
    with pytest.raises(Conflict):
        ambient.save(first)
    assert ambient.preferences() == saved


@pytest.mark.parametrize(
    "patch",
    [
        {"show_clock": 1},
        {"font_scale": True},
        {"font_scale": 0},
        {"font_scale": 4},
        {"idle_seconds": 9},
        {"idle_seconds": 301},
        {"revision": True},
        {"home": "/other"},
        {"metric": "invented"},
    ],
)
def test_invalid_preferences_leave_saved_state_intact(ambient, patch):
    first = ambient.preferences()
    with pytest.raises(ValueError):
        ambient.save({**first, **patch})
    assert ambient.preferences() == first


def test_concurrent_preferences_have_one_revision_winner(ambient):
    first = ambient.preferences()

    def save(scale):
        try:
            return ambient.save({**first, "font_scale": scale})
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [2, 3]))
    assert sum(row is not None for row in results) == 1
    assert ambient.preferences()["revision"] == 2


@pytest.mark.asyncio
async def test_empty_sources_are_not_invented_vitals_and_reads_do_not_create_other_domain_stores(
    ambient, tmp_path
):
    result = await ambient.snapshot()
    assert datetime.fromisoformat(result["observed_at"]).tzinfo is not None
    assert result["cards"]["health"]["rows"] == []
    assert result["cards"]["progress"]["age"] is None
    assert result["cards"]["progress"]["state"] == "unavailable"
    assert result["cards"]["progress"]["tasks_done"] is None
    assert result["cards"]["progress"]["planned_completed_minutes"] is None
    assert result["cards"]["mortality"]["state"] == "unavailable"
    assert result["cards"]["external_calendar"]["state"] == "unavailable"
    assert result["cards"]["digest"]["state"] == "uninstalled"
    assert result["cards"]["goals"]["rows"] == []
    assert result["cards"]["calendar"]["rows"] == []
    assert not (tmp_path / "capabilities/identity/goals.sqlite3").exists()
    assert not (tmp_path / "capabilities/wellbeing.sqlite3").exists()


@pytest.mark.asyncio
async def test_actual_schedule_and_native_task_changes_reach_display(ambient, tmp_path):
    triggers = TriggerStore(tmp_path)
    trigger = Trigger(
        id="daily",
        name="Daily review",
        kind="clock",
        spec={"kind": "cron", "expr": "0 8 * * *"},
        next_fire_at="2030-01-01T08:00:00+00:00",
    )
    triggers.save_all([trigger])
    provider = NativeTaskProvider()
    task = await provider.create_task(title="Write release notes")
    first = await ambient.snapshot()
    assert first["cards"]["schedule"]["rows"][0]["title"] == "Daily review"
    assert first["cards"]["work"]["rows"][0]["id"] == task.id
    assert first["cards"]["work"]["rows"][0]["title"] == "Write release notes"
    trigger.name = "Evening review"
    trigger.enabled = False
    triggers.save_all([trigger])
    second = await ambient.snapshot()
    assert second["cards"]["schedule"]["rows"][0]["title"] == "Evening review"
    assert second["cards"]["schedule"]["rows"][0]["enabled"] is False
    triggers.save_all([])
    assert (await ambient.snapshot())["cards"]["schedule"]["rows"] == []


@pytest.mark.asyncio
async def test_actual_goals_sessions_measurements_and_progress_are_projected_without_notes(
    ambient, tmp_path
):
    goals = GoalStore(tmp_path / "capabilities/identity/goals.sqlite3")
    goal = goals.save_goal(title="Learn piano", request_id="goal")
    session = goals.save_session(
        goal_id=goal["id"],
        title="Scales",
        start_at="2026-01-01T09:00:00+00:00",
        end_at="2026-01-01T09:30:00+00:00",
        request_id="session",
        status="completed",
    )
    progress = ProgressStore(tmp_path / "capabilities/identity/progress.sqlite3")
    progress.configure(
        birth_date=None,
        timezone="UTC",
        tracked_task_ids=[],
        expected_revision=0,
        request_id="progress",
    )
    health = MeasurementStore(tmp_path)
    measurement = health.create(
        {
            "request_id": "weight",
            "kind": "body_weight",
            "observed_at": "2026-01-01T08:00:00+00:00",
            "unit": "kg",
            "values": {"weight": 72},
            "source": "manual",
            "notes": "Private note not needed on ambient screen",
        }
    )
    result = await ambient.snapshot()
    assert result["cards"]["goals"]["rows"] == [
        {"id": goal["id"], "title": "Learn piano", "status": "active"}
    ]
    assert result["cards"]["calendar"]["rows"][0]["id"] == session["id"]
    assert result["cards"]["calendar"]["rows"][0]["start_at"] == session["start_at"]
    assert result["cards"]["health"]["rows"][0]["id"] == measurement["id"]
    assert result["cards"]["health"]["rows"][0]["values"] == {"weight": 72}
    assert "notes" not in result["cards"]["health"]["rows"][0]
    assert result["cards"]["progress"]["age"] is None
    assert result["cards"]["progress"]["planned_completed_minutes"] == 30
    assert result["cards"]["mortality"]["state"] == "unavailable"
    assert "Private note" not in json.dumps(result)


@pytest.mark.asyncio
async def test_scope_change_refuses_other_home_projections(
    ambient, tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "other"))
    with pytest.raises(Conflict, match="scope"):
        await ambient.snapshot()
    assert (
        AmbientDisplay(ExperienceStore(tmp_path / "other")).preferences()["revision"]
        == 1
    )


@pytest.mark.asyncio
async def test_corrupt_goals_source_reports_error_not_zero(ambient, tmp_path):
    path = tmp_path / "capabilities/identity/goals.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a database")
    result = await ambient.snapshot()
    assert result["cards"]["goals"]["state"] == "error"
    assert result["cards"]["calendar"]["state"] == "error"
    assert result["cards"]["goals"]["error"]
    assert result["cards"]["health"]["state"] == "ready"


@pytest.mark.asyncio
async def test_native_tools_and_http_share_preferences_and_source(ambient):
    provider = ExperienceTools(ambient.store)
    first = await provider.invoke("experience_ambient_get", {})
    assert first.success
    preferences = json.loads(first.output)["preferences"]
    saved = await provider.invoke(
        "experience_ambient_update", {**preferences, "show_clock": False}
    )
    assert saved.success
    assert json.loads(saved.output)["preferences"]["revision"] == 2
    app = web.Application()
    app[STORE] = ambient.store
    register(app)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/experience/ambient"
        get = await client.get(base)
        assert get.status == 200
        result = await get.json()
        assert result["preferences"]["show_clock"] is False
        changed = await client.put(
            base, json={**result["preferences"], "font_scale": 3}
        )
        assert changed.status == 200
        assert (await changed.json())["preferences"]["revision"] == 3
        assert (await client.put(base, json=preferences)).status == 409
        assert (await client.put(base, json=[])).status == 400
        assert (
            await client.put(base, json={**preferences, "source_text": "invented"})
        ).status == 400


@pytest.mark.asyncio
async def test_completed_sessions_without_progress_profile_do_not_turn_into_zero_effort(
    ambient, tmp_path
):
    goals = GoalStore(tmp_path / "capabilities/identity/goals.sqlite3")
    goal = goals.save_goal(title="Practice", request_id="goal")
    goals.save_session(
        goal_id=goal["id"],
        title="Completed practice",
        start_at="2026-01-01T09:00:00+00:00",
        end_at="2026-01-01T10:00:00+00:00",
        request_id="session",
        status="completed",
    )
    result = await ambient.snapshot()
    assert result["cards"]["calendar"]["rows"][0]["title"] == "Completed practice"
    assert result["cards"]["progress"]["state"] == "unavailable"
    assert result["cards"]["progress"]["planned_completed_minutes"] is None
    assert not (tmp_path / "capabilities/identity/progress.sqlite3").exists()
