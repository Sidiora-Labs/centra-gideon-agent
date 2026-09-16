import asyncio
import json

from gideon.automation.triggers.store import TriggerStore
from gideon.core.config import AppConfig
from gideon.engine import gateway

TASK_FIELDS = ("_clock_task", "_reaper_task", "_file_watch_task", "_web_watch_task")


def test_disabled_boot_does_not_create_tasks_or_trigger_files():
    async def exercise():
        runtime = gateway.RuntimeCoordinator(AppConfig(), no_crons=True)
        home = gateway.config_dir()
        before = {path.relative_to(home) for path in home.rglob("*")}
        await runtime._init_cron()
        assert all(getattr(runtime, name) is None for name in TASK_FIELDS)
        assert {path.relative_to(home) for path in home.rglob("*")} == before

    asyncio.run(exercise())


def test_real_boot_migrates_reconciles_arms_and_owns_four_tasks():
    async def exercise():
        runtime = gateway.RuntimeCoordinator(AppConfig())
        home = gateway.config_dir()
        legacy = home / "crons.json"
        legacy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "jobs": [
                        {
                            "id": "operator-job",
                            "name": "Operator",
                            "enabled": True,
                            "schedule": {"kind": "cron", "cron_expr": "0 * * * *"},
                            "action": {
                                "provider": "notify",
                                "config": {"message": "Review"},
                            },
                        }
                    ],
                }
            )
        )
        original = legacy.read_bytes()
        await runtime._init_cron()
        tasks = [getattr(runtime, field) for field in TASK_FIELDS]
        try:
            assert all(
                isinstance(task, asyncio.Task) and not task.done() for task in tasks
            )
            assert len(set(tasks)) == 4
            rows = {
                row.trigger.id: row.trigger
                for row in TriggerStore(base_dir=home).load()
            }
            assert {
                "operator-job",
                "system:notification-digest",
                "system:usage-recap",
                "system:source-digest",
            } <= rows.keys()
            assert rows["operator-job"].enabled and rows["operator-job"].next_fire_at
            assert all(
                rows[key].next_fire_at
                for key in (
                    "system:notification-digest",
                    "system:usage-recap",
                    "system:source-digest",
                )
            )
            assert legacy.read_bytes() == original
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert all(task.done() for task in tasks)

    asyncio.run(exercise())
