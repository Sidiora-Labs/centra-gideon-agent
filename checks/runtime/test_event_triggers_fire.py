"""Canonical event rows route through ordered admission and store dispatch."""

from __future__ import annotations

import asyncio
import time

from gideon.automation.event_triggers import SOURCE_INBOX, SOURCE_MEMORY, emit_event
from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers import tools
from gideon.automation.triggers.event_fire import attach, detach
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import AppConfig
from gideon.integrations.action_providers import services as action_services
from gideon.integrations.action_providers.services import ActionServices
from gideon.interfaces.dashboard.state import ConsoleState


def _create(store: TriggerStore, *, max_fires: int = 0, debounce_secs: int = 0) -> str:
    result = tools.create(
        store,
        name="Acme watch",
        kind="event",
        spec={
            "source": "memory",
            "pattern": "MemoryKeyPattern",
            "key_glob": "project.acme.*",
            "max_fires": max_fires,
        },
        workflow={
            "inline": {
                "provider": "notify",
                "config": {"title_template": "Acme changed: $key"},
            }
        },
        created_by="user",
    )
    assert result.ok, result.text
    trigger_id = str(result.data["trigger"]["id"])
    if debounce_secs:
        updated = tools.update(
            store,
            trigger_id=trigger_id,
            patch={"gates": {"debounce_secs": debounce_secs}},
        )
        assert updated.ok, updated.text
    return trigger_id


def _runtime():
    from gideon.engine.gateway import RuntimeCoordinator

    state = ConsoleState(sessions=None, start_time=time.time())
    runtime = RuntimeCoordinator(AppConfig.load(), no_crons=True)
    runtime.dashboard_state = state
    previous_services = action_services.get_action_services()
    action_services.set_action_services(
        ActionServices(state=state, spawn_background=asyncio.create_task)
    )
    return runtime, state, previous_services


def _restore_services(previous) -> None:
    action_services.set_action_services(previous)


def test_matching_bus_event_runs_once_through_store_dispatch_and_history(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def scenario() -> tuple[str, ConsoleState, list[dict]]:
        store = TriggerStore(base_dir=tmp_path)
        trigger_id = _create(store, max_fires=1)
        runtime, state, previous = _runtime()
        router = attach(store, runtime._fire_store_trigger, asyncio.get_running_loop())
        try:
            emit_event(
                source=SOURCE_INBOX,
                event_type="message_received",
                key="C1_1",
                value="project.acme.deadline",
                now=time.time(),
                meta={"sender": "alice"},
            )
            emit_event(
                source=SOURCE_MEMORY,
                event_type="MemoryUpdate",
                key="project.acme.deadline",
                value="Friday",
                now=time.time(),
            )
            await router.settle()
            emit_event(
                source=SOURCE_MEMORY,
                event_type="MemoryUpdate",
                key="project.acme.other",
                value="Saturday",
                now=time.time(),
            )
            await router.settle()
            rows, _ = await ExecutionJournal(tmp_path).list_for_job(trigger_id, 0, 10)
            return trigger_id, state, rows
        finally:
            await detach(router)
            _restore_services(previous)

    trigger_id, state, history = asyncio.run(scenario())
    delivered = [
        row
        for row in state._notification_log
        if row.get("title", "").startswith("Acme changed")
    ]
    assert [row["title"] for row in delivered] == [
        "Acme changed: project.acme.deadline"
    ]
    assert [row["status"] for row in history] == ["success"]
    stored = TriggerStore(base_dir=tmp_path).get(trigger_id)
    assert stored is not None
    assert stored.trigger.run_count == 1
    assert stored.trigger.enabled is False


def test_suppressed_event_leaves_typed_history(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def scenario() -> tuple[str, list[dict]]:
        store = TriggerStore(base_dir=tmp_path)
        trigger_id = _create(store, debounce_secs=60)
        runtime, _state, previous = _runtime()
        router = attach(store, runtime._fire_store_trigger, asyncio.get_running_loop())
        try:
            for value in ("Friday", "Saturday"):
                emit_event(
                    source=SOURCE_MEMORY,
                    event_type="MemoryUpdate",
                    key="project.acme.deadline",
                    value=value,
                    now=time.time(),
                )
                await router.settle()
            rows, _ = await ExecutionJournal(tmp_path).list_for_job(trigger_id, 0, 10)
            return trigger_id, rows
        finally:
            await detach(router)
            _restore_services(previous)

    trigger_id, history = asyncio.run(scenario())
    assert [row["status"] for row in history] == ["skipped_gate", "success"]
    stored = TriggerStore(base_dir=tmp_path).get(trigger_id)
    assert stored is not None and stored.trigger.run_count == 1


def test_matching_event_without_router_spools_then_replays_once(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = TriggerStore(base_dir=tmp_path)
    trigger_id = _create(store)
    emit_event(
        source=SOURCE_MEMORY,
        event_type="MemoryUpdate",
        key="project.acme.deadline",
        value="Friday",
        now=time.time(),
    )
    spool = tmp_path / "trigger-spool.jsonl"
    assert len(spool.read_text(encoding="utf-8").splitlines()) == 1

    async def scenario() -> tuple[ConsoleState, list[dict]]:
        runtime, state, previous = _runtime()
        router = attach(store, runtime._fire_store_trigger, asyncio.get_running_loop())
        try:
            from gideon.automation.triggers.loop import _drain_spool

            assert _drain_spool() == 1
            await router.settle()
            rows, _ = await ExecutionJournal(tmp_path).list_for_job(trigger_id, 0, 10)
            return state, rows
        finally:
            await detach(router)
            _restore_services(previous)

    state, history = asyncio.run(scenario())
    delivered = [
        row
        for row in state._notification_log
        if row.get("title", "").startswith("Acme changed")
    ]
    assert [row["title"] for row in delivered] == [
        "Acme changed: project.acme.deadline"
    ]
    assert [row["status"] for row in history] == ["success"]
    stored = TriggerStore(base_dir=tmp_path).get(trigger_id)
    assert stored is not None and stored.trigger.run_count == 1
    assert not spool.read_text(encoding="utf-8").strip()


def test_legacy_event_rows_keep_identity_budget_and_remain_disarmed(
    tmp_path, monkeypatch
):
    import json

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "event_triggers.json").write_text(
        json.dumps(
            [
                {
                    "id": "acme-note",
                    "name": "Acme note",
                    "pattern": "MemoryKeyPattern",
                    "source": "memory",
                    "key_glob": "project.acme.*",
                    "action_provider": "notify",
                    "action_config": {"title_template": "Acme changed"},
                    "enabled": True,
                    "max_fires": 5,
                    "fire_count": 2,
                    "debounce_secs": 7,
                }
            ]
        ),
        encoding="utf-8",
    )
    from gideon.automation.triggers.boot_migrate import migrate_and_arm

    report = migrate_and_arm(tmp_path)
    assert "event:acme-note" in report["imported"]
    row = TriggerStore(base_dir=tmp_path).get("event:acme-note")
    assert row is not None
    assert row.trigger.name == "Acme note"
    assert row.trigger.spec["key_glob"] == "project.acme.*"
    assert row.trigger.gates == {"debounce_secs": 7, "max_fires": 5}
    assert row.trigger.run_count == 2
    assert row.trigger.enabled is False
    assert row.trigger.capabilities == {}
    assert not (tmp_path / "event_triggers.json").exists()
    assert (tmp_path / "event_triggers.json.migrated").exists()
