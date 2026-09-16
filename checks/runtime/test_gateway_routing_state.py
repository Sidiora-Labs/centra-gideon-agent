import asyncio
import logging
import time

from gideon.automation.triggers.models import Trigger
from gideon.core.config import AppConfig
from gideon.engine.automation_routes import AutomationRoutes
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.state import ConsoleState, _load_notifications
from gideon.security.guardrails.budgets import get_meter


def runtime():
    coordinator = RuntimeCoordinator(AppConfig())
    coordinator.sessions = ConversationDirectory(coordinator.config)
    coordinator.dashboard_state = ConsoleState(coordinator.sessions, time.time())
    return coordinator


def test_instances_keep_separate_launch_and_mutable_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    first = RuntimeCoordinator(
        AppConfig(), no_dashboard=True, json_ready=True, approval_mode="reads"
    )
    second = RuntimeCoordinator(AppConfig())
    first._background_tasks.add("owned")
    first._pending_queue["session"] = ["message"]
    assert second._background_tasks == set() and second._pending_queue == {}
    assert first._json_ready and first._no_dashboard and first._approval_mode == "reads"
    assert not second._json_ready and not second._no_dashboard
    assert second._approval_mode is None
    assert first.config is first._cfg


def test_clock_file_and_chain_routes_reach_actual_notification_provider(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        coordinator = runtime()
        routes = AutomationRoutes(coordinator, logging.getLogger(__name__))
        for identity, kind, spec in (
            ("clock:parent", "clock", {"kind": "interval", "interval_secs": 60}),
            ("file:changed", "file", {"paths": [str(tmp_path / "watched" / "**")]}),
            (
                "run_completed:child",
                "run_completed",
                {"source_trigger": "clock:parent"},
            ),
        ):
            routes.store.upsert(
                Trigger(
                    id=identity,
                    name=identity,
                    kind=kind,
                    enabled=True,
                    spec=spec,
                    delivery="none",
                    capabilities={"providers": ["notify"]},
                    workflow={
                        "provider": "notify",
                        "config": {
                            "title_template": "$EVENT",
                            "body_template": "$trigger_id",
                        },
                    },
                )
            )
        previous = get_action_services()
        set_action_services(
            ActionServices(coordinator.dashboard_state, asyncio.create_task)
        )
        try:
            assert await routes._runner({"trigger_id": "unknown"}) == {
                "status": "error"
            }
            assert await routes._runner({"trigger_id": "clock:parent"}) == {
                "status": "launched"
            }
            await coordinator._fire_file_trigger({"trigger_id": "file:changed"})
            titles = [note["title"] for note in _load_notifications()]
            assert titles.count("trigger.fired") == 1
            assert titles.count("trigger.chained") == 1
            assert titles.count("file.changed") == 1
            child = routes.store.get("run_completed:child").trigger
            assert child.last_success_at
            parent = routes.store.get("clock:parent").trigger
            await routes.cascade(parent, {"chain_path": ["run_completed:child"]})
            assert [note["title"] for note in _load_notifications()] == titles
        finally:
            set_action_services(previous)

    asyncio.run(exercise())


def test_empty_clock_and_reaper_tasks_cancel_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    async def exercise():
        coordinator = runtime()
        tasks = [
            asyncio.create_task(coordinator._clock_loop()),
            asyncio.create_task(coordinator._trigger_reaper_loop()),
        ]
        await asyncio.sleep(0)
        assert all(not task.done() for task in tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert all(task.cancelled() for task in tasks)

    asyncio.run(exercise())


def test_real_daily_spend_and_config_change_rearm_notification(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    cfg = AppConfig()
    cfg.guardrails.budgets.max_tokens_per_day = 100
    cfg.save()
    coordinator = runtime()
    get_meter().charge(120, 0)
    assert coordinator._day_budget_exceeded(context="Scheduled report")
    assert coordinator._day_budget_exceeded(context="Scheduled report")
    assert len(_load_notifications()) == 1
    cfg.guardrails.budgets.max_tokens_per_day = 1000
    cfg.save()
    assert not coordinator._day_budget_exceeded(context="Scheduled report")
    assert not coordinator._budget_notified
    cfg.guardrails.budgets.max_tokens_per_day = 100
    cfg.save()
    assert coordinator._day_budget_exceeded(context="Scheduled report")
    assert len(_load_notifications()) == 2
