"""Native review reservations, process death, and deferred manual completion."""

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_trigger_completion_lifecycle import one_shot

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers import grants
from gideon.automation.triggers.review import TriggerReviewStore
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import defs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.automation_boot import AutomationBoot
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.trigger_outcomes import TriggerPublication
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.handlers.triggers import _dispatch_store_action
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import YOU


def observations(trigger, now):
    return {"review": {"rows": [{"trigger_id": trigger.id, "scheduled_for": now - 1}]}}


@pytest.mark.asyncio
async def test_actual_live_and_dead_process_review_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    now = time.time()
    trigger = one_shot("clock:crash", now)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    reviews = TriggerReviewStore(tmp_path)
    report = observations(trigger, now)
    card = reviews.add_boot_observations(store, report, [], now=now)[0]
    code = (
        "import sys,time; from gideon.automation.triggers.review import TriggerReviewStore; "
        "r=TriggerReviewStore(sys.argv[1]); assert r.begin_run(sys.argv[2]); "
        "print('ready',flush=True); time.sleep(30)"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "runtime"))
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), card["id"]],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert await asyncio.to_thread(child.stdout.readline) == "ready\n"
        assert reviews.get(card["id"])["owner_pid"] == child.pid
        trigger.run_owner_pid = child.pid
        store.upsert(trigger)
        boot = AutomationBoot(
            RuntimeCoordinator(AppConfig()),
            home=lambda: tmp_path,
            logger=logging.getLogger(__name__),
        )
        assert await boot.recover_interrupted(store) == []
        reviews.add_boot_observations(store, report, [], now=now + 2)
        assert reviews.get(card["id"])["status"] == "running"
        assert reviews.begin_run(card["id"]) is None
        child.terminate()
        await asyncio.to_thread(child.wait, timeout=5)
        interrupted = await boot.recover_interrupted(store)
        assert interrupted == [trigger.id]
        assert await boot.recover_interrupted(store) == []
        reviews.add_boot_observations(store, report, interrupted, now=now + 3)
        assert reviews.get(card["id"])["status"] == "pending"
        assert reviews.begin_run(card["id"]) is not None
        assert reviews.begin_run(card["id"]) is None
        rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert count == 1 and rows[0]["status"] == "interrupted"
    finally:
        if child.poll() is None:
            child.terminate()
            await asyncio.to_thread(child.wait, timeout=5)
        child.stdout.close()


@pytest.mark.asyncio
async def test_manual_review_native_workflow_waits_for_terminal_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.handlers.triggers.config_dir", lambda: tmp_path
    )
    now = time.time()
    trigger = one_shot("clock:manual-review", now)
    trigger.enabled = False
    trigger.next_fire_at = ""
    trigger.workflow = {
        "provider": "run-workflow",
        "config": {"workflow": "review-empty"},
    }
    previous_provider = defs.get_provider("native")
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    await provider.save_def(
        name="review-empty",
        root={"kind": "sequence", "children": []},
        provenance="user",
    )
    question = grants.question(trigger)
    assert grants.grant(
        trigger,
        confirmed_revision=question.revision,
        principal=YOU,
        shown=question.shown,
    )
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    reviews = TriggerReviewStore(tmp_path)
    card = reviews.add_boot_observations(
        store, observations(trigger, now), [], now=now
    )[0]
    assert reviews.begin_run(card["id"])
    state = ConsoleState(None, now)
    supervisor = WorkflowWatchdog()
    previous_services = get_action_services()
    set_action_services(
        ActionServices(state, asyncio.create_task, workflows=supervisor)
    )
    try:
        from trigger_origin_fixture import authenticated_origin

        origin = await authenticated_origin()
        ok, note = await _dispatch_store_action(
            trigger,
            {"trigger_id": trigger.id, "review_id": card["id"]},
            event="restart.review",
            accepted_origin=origin,
        )
        assert ok and note == "launched"
        assert reviews.get(card["id"])["status"] == "running"
        assert (
            not TriggerStore(base_dir=tmp_path).get(trigger.id).trigger.last_success_at
        )
        assert state._background_tasks
        await asyncio.wait_for(asyncio.gather(*state._background_tasks), timeout=5)
        resolved = reviews.get(card["id"])
        assert resolved["status"] == "resolved" and resolved["outcome"] == "ran_late"
        assert TriggerStore(base_dir=tmp_path).get(trigger.id) is None
        rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert count == 3 and [row["status"] for row in rows].count("launched") == 1
        assert all(
            row["status"] == "success" for row in rows if row["status"] != "launched"
        )
    finally:
        await supervisor.stop()
        set_action_services(previous_services)
        if previous_provider is None:
            defs.unregister_provider("native")
        else:
            defs.register_provider(previous_provider)


def test_missed_review_is_one_durable_inbox_item(tmp_path, monkeypatch):
    from gideon.integrations.inbox import InboxStore

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    now = time.time()
    trigger = one_shot("clock:notice", now)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    reviews = TriggerReviewStore(tmp_path)
    report = observations(trigger, now)
    card = reviews.add_boot_observations(store, report, [], now=now)[0]
    runtime = RuntimeCoordinator(AppConfig())
    runtime.dashboard_state = ConsoleState(None, now)
    publication = TriggerPublication(runtime, logging.getLogger(__name__))
    publication.missed(report)
    publication.missed(report)
    inbox = InboxStore()
    inbox.load()
    matching = [
        item
        for item in inbox.items.values()
        if item.refs.get("trigger_review") == card["id"]
    ]
    assert len(matching) == 1
    previous = get_action_services()
    set_action_services(ActionServices(runtime.dashboard_state, asyncio.create_task))
    try:
        assert reviews.resolve(card["id"], decision="dismiss", outcome="dismissed")
        assert not reviews.resolve(card["id"], decision="dismiss", outcome="dismissed")
        inbox.load()
        assert inbox.items[matching[0].id].status == "handled"
    finally:
        set_action_services(previous)
