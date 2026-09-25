import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.service import tick
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.registry import register_action_provider
from gideon.integrations.inbox import InboxStore
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_life import register
from gideon.workspace.capabilities.wellbeing.cognition import CognitiveStore
from gideon.workspace.capabilities.wellbeing.life_calendar import LifeCalendarStore
from gideon.workspace.capabilities.wellbeing.life_provider import (
    WellbeingReminderAction,
)
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def config(**changes):
    value = dict(
        request_id="config",
        revision=0,
        birth_date="2000-02-29",
        horizon_years=80,
        sleep_hours=8,
        timezone="UTC",
        budgets=[dict(name="Study", hours_per_week=14)],
        source="my planning assumptions",
        reminder=dict(enabled=False, time="00:00"),
    )
    value.update(changes)
    return value


def event(**changes):
    value = dict(
        request_id="event",
        date="2026-09-25",
        title="Completed course",
        notes="Personal milestone",
        kind="recorded",
        source="journal",
    )
    value.update(changes)
    return value


def test_config_revision_trigger_and_projection_arithmetic(tmp_path):
    store = LifeCalendarStore(tmp_path)
    assert store.get_config() is None
    assert store.projection() == {"configured": False}
    assert store.config_history() == []
    row = store.configure(config())
    assert row["assumption"] == "user_declared_horizon"
    assert row["revision"] == 1
    assert row["source"] == "my planning assumptions"
    assert row["trigger_id"] == "wellbeing-daily-completion"
    loaded = TriggerStore(tmp_path).get(row["trigger_id"])
    assert loaded.ok
    assert loaded.trigger.enabled is False
    assert loaded.trigger.workflow == dict(provider="wellbeing-reminder", config={})
    assert loaded.trigger.capabilities == {"providers": ["wellbeing-reminder"]}
    assert loaded.trigger.spec == dict(kind="cron", expr="0 0 * * *", timezone="UTC")
    assert loaded.trigger.delivery == "none"
    assert loaded.trigger.failure_delivery == "none"
    assert store.configure(config()) == row
    assert LifeCalendarStore(tmp_path).get_config() == row
    projection = store.projection("2000-03-07T12:00:00Z")
    assert projection["elapsed_days"] == 7
    assert projection["weeks_elapsed"] == 1
    assert projection["horizon_date"] == "2080-02-29"
    assert (
        projection["total_days"] == (datetime(2080, 2, 29) - datetime(2000, 2, 29)).days
    )
    remaining = projection["total_days"] - 7
    assert projection["remaining_days"] == remaining
    assert projection["sleep_hours_elapsed"] == 56
    assert projection["sleep_hours_remaining"] == remaining * 8
    assert projection["waking_hours_remaining"] == remaining * 16
    assert projection["budgets"][0]["remaining_hours"] == remaining * 2
    assert projection["events"] == []
    assert store.projection("1999-01-01T00:00:00Z")["elapsed_days"] == 0
    assert store.projection("2099-01-01T00:00:00Z")["remaining_days"] == 0
    updated = store.configure(
        config(
            request_id="updated",
            revision=1,
            horizon_years=81,
            timezone="America/New_York",
        )
    )
    assert updated["created_at"] == row["created_at"]
    assert store.projection("2000-03-01T01:00:00Z")["elapsed_days"] == 0
    assert store.projection()["horizon_date"] == "2081-02-28"
    assert store.config_history() == [row, updated]
    with pytest.raises(MeasurementError, match="changed"):
        store.configure(config(request_id="stale"))


def test_event_corrections_tombstones_and_history(tmp_path):
    store = LifeCalendarStore(tmp_path)
    original = store.create_event(event())
    assert original["revision"] == 1
    assert original["deleted"] is False
    assert store.create_event(event()) == original
    assert store.list_events() == [original]
    corrected = store.update_event(
        original["id"],
        dict(
            request_id="edit",
            revision=1,
            date="2026-09-26",
            title="Course milestone",
            kind="planned",
        ),
    )
    assert corrected["source"] == original["source"]
    assert corrected["notes"] == original["notes"]
    assert corrected["created_at"] == original["created_at"]
    assert corrected["revision"] == 2
    assert corrected["kind"] == "planned"
    assert LifeCalendarStore(tmp_path).list_events() == [corrected]
    with pytest.raises(MeasurementError, match="changed"):
        store.update_event(
            original["id"], dict(request_id="stale", revision=1, title="stale")
        )
    with pytest.raises(MeasurementError, match="immutable"):
        store.update_event(
            original["id"], dict(request_id="source", revision=2, source="changed")
        )
    deleted = store.update_event(
        original["id"], dict(request_id="delete", revision=2, deleted=True)
    )
    assert store.list_events() == []
    assert store.history_event(original["id"]) == [original, corrected, deleted]
    with pytest.raises(MeasurementError, match="deleted"):
        store.update_event(
            original["id"], dict(request_id="restore", revision=3, deleted=False)
        )


def test_local_reminder_real_inbox_claims_restart_and_completion(tmp_path):
    store = LifeCalendarStore(tmp_path)
    assert store.check_reminder() == {"status": "disabled"}
    store.configure(config(reminder=dict(enabled=True, time="00:00")))
    trigger = TriggerStore(tmp_path).get("wellbeing-daily-completion").trigger
    assert trigger.enabled
    assert trigger.next_fire_at
    first = store.check_reminder()
    assert first["status"] == "sent"
    inbox = InboxStore(tmp_path / "inbox.json")
    inbox.load()
    assert first["item_id"] in inbox.items
    item = inbox.items[first["item_id"]]
    assert item.item_kind == "needs_input"
    assert item.refs["local_date"] == datetime.now(timezone.utc).date().isoformat()
    assert item.refs["href"] == "#/capabilities/wellbeing/cognition"
    assert "Daily cognitive practice" in item.message
    assert item.can_reply is False
    repeated = LifeCalendarStore(tmp_path).check_reminder()
    assert repeated["status"] == "already_sent"
    assert repeated["item_id"] == first["item_id"]
    inbox.update(first["item_id"], status="handled")
    inbox.flush()
    assert store.check_reminder()["item_id"] == first["item_id"]
    with store.connection() as db:
        db.execute("DELETE FROM life_reminder_claims")
    assert store.check_reminder()["status"] == "already_sent"
    inbox.load()
    assert len(inbox.items) == 1
    cognitive = CognitiveStore(tmp_path)
    session = cognitive.start(
        dict(
            request_id="cognitive",
            kind="color_word",
            planned_trials=1,
            time_limit_seconds=60,
        )
    )
    completed = cognitive.answer(
        session["id"],
        dict(
            request_id="complete",
            revision=1,
            answer=session["current_trial"]["stimulus"]["color"],
        ),
    )
    assert completed["status"] == "completed"
    assert store.check_reminder()["status"] == "completed"
    assert len(inbox.items) == 1


def test_reminder_timezone_boundaries_concurrency_and_home_guard(tmp_path):
    store = LifeCalendarStore(tmp_path)
    store.configure(
        config(timezone="Europe/Berlin", reminder=dict(enabled=True, time="09:00"))
    )
    assert store.check_reminder("2026-10-25T07:59:00Z")["status"] == "not_due"
    assert store.check_reminder("2026-10-25T08:00:00Z")["status"] == "sent"
    assert store.check_reminder("2026-10-25T22:30:00Z")["status"] == "already_sent"
    assert store.check_reminder("2026-10-25T23:30:00Z")["status"] == "not_due"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: LifeCalendarStore(tmp_path).check_reminder(
                    "2026-10-26T09:00:00Z"
                ),
                range(4),
            )
        )
    assert sum(row["status"] == "sent" for row in results) == 1
    assert len({row["item_id"] for row in results}) == 1
    other_inbox = InboxStore(tmp_path / "other" / "inbox.json")
    with pytest.raises(MeasurementError, match="different home"):
        store.check_reminder("2026-10-27T09:00:00Z", inbox=other_inbox)
    assert not other_inbox._path.exists()
    with store.connection() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM life_reminder_claims").fetchone()[0] == 2
        )


@pytest.mark.parametrize(
    "changes",
    [
        dict(birth_date="2099-01-01"),
        dict(birth_date="2000-02-30"),
        dict(horizon_years=0),
        dict(horizon_years=True),
        dict(horizon_years=121),
        dict(sleep_hours=-1),
        dict(sleep_hours=25),
        dict(sleep_hours=float("nan")),
        dict(timezone="Unknown/Zone"),
        dict(budgets=[dict(name="Work", hours_per_week=120)]),
        dict(
            budgets=[dict(name="x", hours_per_week=1), dict(name="x", hours_per_week=2)]
        ),
        dict(budgets=[dict(name="x", hours_per_week=True)]),
        dict(reminder=dict(enabled="true", time="09:00")),
        dict(reminder=dict(enabled=True, time="24:00")),
        dict(source=""),
    ],
)
def test_invalid_projection_assumptions_do_not_create_config(tmp_path, changes):
    store = LifeCalendarStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.configure(config(**changes))
    assert store.get_config() is None
    assert TriggerStore(tmp_path).get("wellbeing-daily-completion") is None


def test_isolation_invalid_events_and_config_receipts(tmp_path):
    store = LifeCalendarStore(tmp_path / "one")
    original = store.configure(config())
    assert LifeCalendarStore(tmp_path / "two").get_config() is None
    assert LifeCalendarStore(tmp_path / "two").check_reminder() == {
        "status": "disabled"
    }
    with pytest.raises(MeasurementError, match="Request ID"):
        store.configure(config(source="different"))
    for changes in [
        dict(title=""),
        dict(date="bad"),
        dict(kind="unknown"),
        dict(notes=None),
        dict(source=""),
    ]:
        with pytest.raises(MeasurementError):
            store.create_event(event(**changes))
    assert store.list_events() == []
    assert store.get_config() == original
    with pytest.raises(MeasurementError) as caught:
        store.history_event("missing")
    assert caught.value.status == 404


@pytest.mark.asyncio
async def test_actual_clock_dispatch_journal_and_persisted_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = LifeCalendarStore(tmp_path)
    store.configure(config(reminder=dict(enabled=True, time="00:00")))
    provider = WellbeingReminderAction(tmp_path)
    register_action_provider(provider)
    trigger_store = TriggerStore(tmp_path)
    trigger = trigger_store.get("wellbeing-daily-completion").trigger
    scheduled = datetime.fromisoformat(trigger.next_fire_at).timestamp()
    due = await tick(trigger_store, now=scheduled + 1, persist=True, base_dir=tmp_path)
    assert len(due.fires) == 1
    fire = due.fires[0]
    runtime = RuntimeCoordinator(
        AppConfig(), no_dashboard=True, no_crons=True, no_open=True
    )
    await runtime._fire_store_trigger(
        fire.trigger, {"scheduled_for": fire.scheduled_for}
    )
    runs, total = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
    assert total == 1
    assert runs[0]["status"] == "success"
    inbox = InboxStore(tmp_path / "inbox.json")
    inbox.load()
    assert len(inbox.items) == 1
    item = next(iter(inbox.items.values()))
    assert (
        item.refs["local_date"]
        == datetime.fromtimestamp(scheduled, timezone.utc).date().isoformat()
    )
    repeated = await provider.execute(
        {}, ActionContext(event="trigger.fired", payload={"scheduled_for": scheduled})
    )
    assert repeated.success
    assert json.loads(repeated.stdout)["status"] == "already_sent"
    forbidden = await provider.execute(
        {"home": "/other"}, ActionContext(event="trigger.fired")
    )
    assert not forbidden.success
    assert "overrides" in forbidden.error


@pytest.mark.asyncio
async def test_actual_http_projection_milestones_and_inbox(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/wellbeing/life"
        assert await (await client.get(base + "/config")).json() is None
        assert await (await client.get(base + "/projection")).json() == {
            "configured": False
        }
        response = await client.put(
            base + "/config", json=config(reminder=dict(enabled=True, time="00:00"))
        )
        assert response.status == 200
        settings = await response.json()
        assert settings["revision"] == 1
        response = await client.get(base + "/config/history")
        assert (await response.json())["history"] == [settings]
        response = await client.post(base + "/events", json=event())
        row = await response.json()
        assert row["source"] == "journal"
        response = await client.put(
            base + "/events/" + row["id"],
            json=dict(request_id="edit", revision=1, title="Revised milestone"),
        )
        assert response.status == 200
        response = await client.get(base + "/events/" + row["id"] + "/history")
        assert len((await response.json())["history"]) == 2
        response = await client.get(
            base + "/projection", params={"as_of": "2026-09-25T12:00:00Z"}
        )
        projection = await response.json()
        assert projection["events"][0]["title"] == "Revised milestone"
        response = await client.post(base + "/reminder/check", json={})
        assert (await response.json())["status"] == "sent"
        response = await client.post(base + "/reminder/check", json={})
        assert (await response.json())["status"] == "already_sent"
        assert (
            await client.post(
                base + "/reminder/check", json={"as_of": "2099-01-01T00:00:00Z"}
            )
        ).status == 400
        assert (
            await client.put(
                base + "/config",
                data="bad",
                headers={"Content-Type": "application/json"},
            )
        ).status == 400
        assert (await client.get(base + "/projection?as_of=bad")).status == 400


@pytest.mark.asyncio
async def test_native_life_calendar_operations(tmp_path):
    provider = WellbeingProvider(tmp_path)

    async def invoke(operation, identity=None, payload=None):
        result = await provider.invoke(
            "wellbeing_records",
            dict(operation="life_" + operation, id=identity, payload=payload or {}),
        )
        assert result.success, result.error
        return json.loads(result.output)

    assert await invoke("config") is None
    row = await invoke("configure", payload=config())
    assert await invoke("config_history") == [row]
    projection = await invoke("projection", payload=dict(as_of="2000-03-07T12:00:00Z"))
    assert projection["elapsed_days"] == 7
    milestone = await invoke("event_create", payload=event())
    updated = await invoke(
        "event_update",
        milestone["id"],
        dict(request_id="edit", revision=1, notes="new note"),
    )
    assert await invoke("events") == [updated]
    assert await invoke("event_history", milestone["id"]) == [milestone, updated]
    assert await invoke("reminder_check") == {"status": "disabled"}
    forbidden = await provider.invoke(
        "wellbeing_records",
        dict(
            operation="life_reminder_check", payload={"as_of": "2099-01-01T00:00:00Z"}
        ),
    )
    assert not forbidden.success
    assert "payload" in forbidden.error
    unknown = await provider.invoke("wellbeing_records", dict(operation="life_unknown"))
    assert not unknown.success
