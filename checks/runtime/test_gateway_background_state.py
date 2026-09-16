import asyncio
import logging
import time

from gideon.automation.triggers.models import Trigger
from gideon.core.config import AppConfig
from gideon.engine.background_passes import (
    AutonomySweep,
    KnowledgeMaintenance,
    WatchPoll,
)
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.state import ConsoleState, _load_notifications
from gideon.security.guardrails import incident


def coordinator():
    runtime = RuntimeCoordinator(AppConfig())
    runtime.sessions = ConversationDirectory(runtime.config)
    runtime.dashboard_state = ConsoleState(runtime.sessions, time.time())
    runtime._last_autonomy_scan = time.monotonic()
    return runtime


def test_real_file_cycle_defers_changes_during_incident_then_delivers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))

    async def exercise():
        runtime = coordinator()
        poll = WatchPoll(runtime, web=False, logger=logging.getLogger(__name__))
        watched = tmp_path / "watched"
        watched.mkdir()
        poll.store.upsert(
            Trigger(
                id="file:real",
                name="Files",
                kind="file",
                enabled=True,
                spec={"paths": [str(watched / "**")]},
                workflow={
                    "provider": "notify",
                    "config": {
                        "title_template": "Changed file",
                        "body_template": "A report arrived",
                    },
                },
                delivery="none",
            )
        )
        previous = get_action_services()
        set_action_services(
            ActionServices(runtime.dashboard_state, asyncio.create_task)
        )
        try:
            await poll.cycle()
            assert not _load_notifications()
            incident.activate("maintenance")
            (watched / "report.md").write_text("Actual document")
            await poll.cycle()
            assert not _load_notifications()
            incident.resume()
            await poll.cycle()
            notifications = _load_notifications()
            matching = [
                note for note in notifications if note["title"] == "Changed file"
            ]
            assert len(matching) == 1 and matching[0]["body"] == "A report arrived"
            await poll.cycle()
            assert _load_notifications() == notifications
        finally:
            incident.resume()
            set_action_services(previous)

    asyncio.run(exercise())


def test_web_loop_empty_store_and_cancellation_use_real_task(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))

    async def exercise():
        runtime = coordinator()
        poll = WatchPoll(runtime, web=True, logger=logging.getLogger(__name__))
        await poll.cycle()
        task = asyncio.create_task(poll.run())
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and not _load_notifications()

    asyncio.run(exercise())


def test_live_queue_probe_reads_current_queue_without_constructing_one():
    runtime = coordinator()
    maintenance = KnowledgeMaintenance(runtime, logging.getLogger(__name__))
    assert maintenance.depth() == 0
    assert runtime.dashboard_state._knowledge_ingest_queue is None
    queue = asyncio.Queue()
    queue.put_nowait("first")
    queue.put_nowait("second")
    runtime.dashboard_state._knowledge_ingest_queue = queue
    assert maintenance.depth() == 2
    queue.get_nowait()
    assert maintenance.depth() == 1
    runtime.dashboard_state = None
    assert maintenance.depth() == 0


def test_autonomy_scan_throttles_from_actual_monotonic_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    runtime = coordinator()
    before = runtime._last_autonomy_scan
    AutonomySweep(runtime, interval=3600, logger=logging.getLogger(__name__)).run()
    assert runtime._last_autonomy_scan == before
    AutonomySweep(runtime, interval=0, logger=logging.getLogger(__name__)).run()
    assert runtime._last_autonomy_scan >= before
