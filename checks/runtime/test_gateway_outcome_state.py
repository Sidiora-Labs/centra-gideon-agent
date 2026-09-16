import asyncio
import time

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers.base import ActionResult
from gideon.interfaces.dashboard.state import ConsoleState, _load_notifications


def runtime_and_store(tmp_path):
    runtime = RuntimeCoordinator(AppConfig())
    runtime.sessions = ConversationDirectory(runtime.config)
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    return runtime, TriggerStore(base_dir=tmp_path)


def test_failure_alert_suppression_keeps_real_failure_streak(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime, store = runtime_and_store(tmp_path)
        trigger = Trigger(
            id="clock:failure",
            name="Daily report",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            workflow={"provider": "notify", "config": {"title_template": "Report"}},
            delivery="inbox",
            failure_policy={"autopause_after": 2, "dedupe_hash": True},
        )
        store.upsert(trigger)
        for _ in range(2):
            await runtime._record_fire_outcome(
                trigger,
                result=ActionResult(success=False, error="Report file is unavailable"),
            )
            runtime._deliver_fire_outcome(
                trigger, ok=False, error="Report file is unavailable"
            )
        rows, total = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert total == 2 and all(row["status"] == "failure" for row in rows)
        current = store.get(trigger.id).trigger
        assert current.state == "autopaused" and not current.enabled
        assert current.last_error_summary == "Report file is unavailable"
        assert current.last_alert_hash and current.last_alert_at > 0
        notes = _load_notifications()
        attention = [
            note for note in notes if note.get("event") == "automation.needs_attention"
        ]
        assert (
            len(attention) == 1 and "Report file is unavailable" in attention[0]["body"]
        )
        assert len(notes) == 2

    asyncio.run(exercise())


def test_transport_parking_and_recovery_write_durable_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime, store = runtime_and_store(tmp_path)
        trigger = Trigger(
            id="clock:network",
            name="Remote feed",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            workflow={"provider": "notify", "config": {"title_template": "Feed"}},
        )
        store.upsert(trigger)
        await runtime._record_fire_outcome(trigger, exc=ConnectionError("feed offline"))
        parked = store.get(trigger.id).trigger
        assert parked.state == "parked" and parked.enabled
        assert parked.park_retry_after > time.time()
        await runtime._record_fire_outcome(parked, result=ActionResult(success=True))
        restored = store.get(trigger.id).trigger
        assert restored.state == "active" and restored.enabled
        assert restored.park_retry_after == 0
        assert restored.last_success_at and restored.last_failure_at
        assert not _load_notifications()

    asyncio.run(exercise())


def test_refusal_vocabulary_rejects_unknown_status_without_io(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        runtime = RuntimeCoordinator(AppConfig())
        trigger = Trigger(id="clock:refused", name="Refused", kind="clock")
        await runtime._record_refused_fire(
            trigger, status="unknown", error="never write"
        )
        assert not list(tmp_path.iterdir())
        await runtime._record_blocked_fire(trigger, "override")
        rows, total = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert total == 1 and rows[0]["status"] == "blocked_injection"
        assert rows[0]["error"].endswith("(override); never retried")

    asyncio.run(exercise())


def test_missed_runs_summary_has_one_real_notification(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    runtime, _ = runtime_and_store(tmp_path)
    runtime._surface_missed_review(
        {
            "review": {
                "rows": [{"trigger_id": "one"}],
                "summaries": [{"trigger_id": "two", "count": "8"}],
                "truncated": True,
            },
            "catch_up": [{"catching_up": True}],
        }
    )
    notes = _load_notifications()
    assert len(notes) == 1
    assert notes[0]["missed"] == 9
    assert notes[0]["triggers"] == 2
    assert notes[0]["caught_up"] == 1
    assert notes[0]["truncated"] is True
