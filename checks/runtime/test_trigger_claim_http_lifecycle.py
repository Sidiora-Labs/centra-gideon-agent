"""Real process lease race and authenticated native HTTP review journeys."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers import claims, grants, parks, reaper
from gideon.automation.triggers.scheduling import Claim
from gideon.automation.triggers.review import TriggerReviewStore
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import defs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.integrations.action_providers.base import ActionResult
from gideon.integrations.action_providers.services import ActionServices, get_action_services, set_action_services
from gideon.interfaces.dashboard.handlers.triggers import _dispatch_store_action, register_trigger_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.security.approval_answer import YOU
from test_trigger_completion_lifecycle import one_shot
from test_trigger_review_completion import observations


@pytest.mark.asyncio
async def test_real_process_claim_race_and_long_live_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    code = (
        "import sys,time,os; from gideon.automation.triggers.claims import acquire_claim; "
        "from gideon.automation.triggers.scheduling import Claim; "
        "print(int(acquire_claim(Claim('clock:race',sys.argv[2],time.time(),.1),owner_pid=os.getpid(),base_dir=sys.argv[1])),flush=True); "
        "time.sleep(30)"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "runtime"))
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), f"holder-{i}"], stdout=subprocess.PIPE, text=True, env=env) for i in range(2)]
    try:
        results = await asyncio.gather(*(asyncio.to_thread(child.stdout.readline) for child in children))
        assert sorted(results) == ["0\n", "1\n"]
        winner = children[results.index("1\n")]
        held = claims.read_claim("clock:race", now=time.time() + 3600, base_dir=tmp_path)
        assert held.owner_pid == winner.pid and held.owner_identity
        assert not held.expired(time.time() + 3600)
        assert not claims.acquire_claim(Claim("clock:race", "different-task", time.time()), owner_pid=os.getpid(), base_dir=tmp_path)
        assert not claims.release_claim("clock:race", base_dir=tmp_path)
        assert not reaper.overdue(now=time.time() + 3600, base_dir=tmp_path)
    finally:
        for child in children:
            child.kill()
            await asyncio.to_thread(child.wait, timeout=5)
            child.stdout.close()
    assert claims.owner_state(winner.pid, held.owner_identity) is False
    assert claims.release_claim("clock:race", base_dir=tmp_path)


@pytest.mark.asyncio
async def test_manual_rejection_has_exact_native_history(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr("gideon.interfaces.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    trigger = one_shot("clock:rejected", time.time())
    trigger.workflow = {"provider": "not-a-provider"}
    TriggerStore(base_dir=tmp_path).upsert(trigger)
    ok, note = await _dispatch_store_action(trigger, {})
    rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
    assert not ok and note.startswith("refused:")
    assert count == 1 and rows[0]["status"] == "skipped_gate"
    assert "not-a-provider" in rows[0]["summary"] and rows[0]["error"]
    assert not TriggerStore(base_dir=tmp_path).get(trigger.id).trigger.last_success_at


@pytest.mark.asyncio
async def test_real_http_review_launch_then_native_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr("gideon.interfaces.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    now = time.time()
    previous_provider = defs.get_provider("native")
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    await provider.save_def(name="http-review", root={"kind": "sequence", "children": [{"kind": "wait", "config": {"duration_secs": 1}}]}, provenance="user")
    trigger = one_shot("clock:http-review", now)
    trigger.enabled, trigger.next_fire_at = False, ""
    trigger.workflow = {"provider": "run-workflow", "config": {"workflow": "http-review"}}
    question = grants.question(trigger)
    assert grants.grant(trigger, confirmed_revision=question.revision, principal=YOU, shown=question.shown)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    reviews = TriggerReviewStore(tmp_path)
    card = reviews.add_boot_observations(store, observations(trigger, now), [], now=now)[0]
    state = ConsoleState(None, now)
    supervisor = WorkflowWatchdog()
    previous_services = get_action_services()
    set_action_services(ActionServices(state, asyncio.create_task, workflows=supervisor))
    app = web.Application(middlewares=[token_auth_middleware(port=19417)])
    app["state"] = state
    register_trigger_routes(app)
    token = generate_token("A01-owner", kind="desktop")
    cookies = {"gideon_token_19417": token}
    body = {"trigger_id": f"store:{trigger.id}", "review_id": card["id"], "decision": "run_now", "expected_revision": card["action_revision"]}
    try:
        async with TestClient(TestServer(app)) as client:
            denied = await client.post("/api/triggers/review", json=body)
            assert denied.status == 403
            launched = await client.post("/api/triggers/review", json=body, cookies=cookies)
            response = await launched.json()
            assert launched.status == 202 and response["outcome"] == "pending"
            pending = await client.get("/api/triggers/review", cookies=cookies)
            assert (await pending.json())["cards"][0]["status"] == "running"
            duplicate = await client.post("/api/triggers/review", json=body, cookies=cookies)
            assert duplicate.status == 409, (await duplicate.text(), reviews.get(card["id"]))
            assert claims.is_running(trigger.id, base_dir=tmp_path)
            await asyncio.wait_for(asyncio.gather(*state._background_tasks), timeout=6)
            assert reviews.get(card["id"])["status"] == "resolved"
            assert not claims.is_running(trigger.id, base_dir=tmp_path)
            assert TriggerStore(base_dir=tmp_path).get(trigger.id) is None
    finally:
        await supervisor.stop()
        set_action_services(previous_services)
        if previous_provider is None:
            defs.unregister_provider("native")
        else:
            defs.register_provider(previous_provider)


@pytest.mark.asyncio
async def test_real_http_park_stale_answer_keeps_receipt_then_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr("gideon.interfaces.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    now = time.time()
    trigger = one_shot("clock:http-park", now)
    trigger.enabled, trigger.next_fire_at = False, ""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    reviews = TriggerReviewStore(tmp_path)
    card = reviews.add_boot_observations(store, observations(trigger, now), [], now=now)[0]
    assert reviews.begin_run(card["id"])
    park = parks.raise_park(trigger, ActionResult(True, outcome="needs_input", stderr="Continue?"))
    assert parks.associate_review(trigger, card["id"])
    state = ConsoleState(None, now)
    previous = get_action_services()
    set_action_services(ActionServices(state, asyncio.create_task))
    app = web.Application(middlewares=[token_auth_middleware(port=19418)])
    app["state"] = state
    register_trigger_routes(app)
    cookies = {"gideon_token_19418": generate_token("A01-park-owner", kind="desktop")}
    body = {"resume_token": park.token, "answer": True}
    try:
        async with TestClient(TestServer(app)) as client:
            changed = store.get(trigger.id).trigger
            changed.workflow["config"]["title_template"] = "changed after question"
            store.upsert(changed)
            stale = await client.post(f"/api/triggers/store:{trigger.id}/answer", json=body, cookies=cookies)
            assert stale.status == 409
            assert parks.load(trigger.id).token == park.token
            assert reviews.get(card["id"])["status"] == "running"
            store.upsert(trigger)
            resumed = await client.post(f"/api/triggers/store:{trigger.id}/answer", json=body, cookies=cookies)
            assert resumed.status == 200 and (await resumed.json())["outcome"] == "resumed"
            assert reviews.get(card["id"])["status"] == "resolved"
            assert parks.load(trigger.id) is None
    finally:
        set_action_services(previous)


@pytest.mark.asyncio
async def test_parallel_actual_process_holders_recover_only_departed(tmp_path, monkeypatch):
    import logging
    from gideon.engine.automation_boot import AutomationBoot
    from gideon.engine.gateway import RuntimeCoordinator
    from gideon.core.config.loader import AppConfig

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    trigger = one_shot("clock:parallel", time.time())
    trigger.overlap = "parallel"
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    code = (
        "import sys,time,os; from gideon.automation.triggers.claims import acquire_claim; "
        "from gideon.automation.triggers.scheduling import Claim; "
        "c=Claim('clock:parallel','worker',time.time(),.1); "
        "assert acquire_claim(c,owner_pid=os.getpid(),overlap='parallel',base_dir=sys.argv[1]); "
        "print(c.holder,flush=True); time.sleep(30)"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "runtime"))
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True, env=env) for _ in range(2)]
    try:
        tickets = await asyncio.gather(*(asyncio.to_thread(child.stdout.readline) for child in children))
        holders = claims.read_claims(trigger.id, base_dir=tmp_path)
        assert len(holders) == 2 and len(set(tickets)) == 2
        assert not claims.acquire_claim(Claim(trigger.id, "manual", time.time()), owner_pid=os.getpid(), base_dir=tmp_path)
        assert not claims.release_claim(trigger.id, holder=tickets[0].strip(), owner_pid=os.getpid(), base_dir=tmp_path)
        assert not reaper.overdue(now=time.time() + 3600, base_dir=tmp_path)
        children[0].kill()
        await asyncio.to_thread(children[0].wait, timeout=5)
        boot = AutomationBoot(RuntimeCoordinator(AppConfig()), home=lambda: tmp_path, logger=logging.getLogger(__name__))
        assert await boot.recover_interrupted(store) == [trigger.id]
        remaining = claims.read_claims(trigger.id, base_dir=tmp_path)
        assert len(remaining) == 1 and remaining[0].owner_pid == children[1].pid
        assert await boot.recover_interrupted(store) == []
        assert claims.is_running(trigger.id, now=time.time() + 3600, base_dir=tmp_path)
        rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert count == 1 and rows[0]["status"] == "interrupted"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                await asyncio.to_thread(child.wait, timeout=5)
            child.stdout.close()


def test_parallel_one_shot_retirement_waits_for_other_holder(tmp_path, monkeypatch):
    from gideon.automation.triggers.service import retire_after_run

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    trigger = one_shot("clock:parallel-once", time.time())
    trigger.enabled, trigger.next_fire_at = False, ""
    trigger.overlap = "parallel"
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    first, second = Claim(trigger.id, "first", time.time()), Claim(trigger.id, "second", time.time())
    assert claims.acquire_claim(first, owner_pid=os.getpid(), overlap=trigger.overlap, base_dir=tmp_path)
    assert claims.acquire_claim(second, owner_pid=os.getpid(), overlap=trigger.overlap, base_dir=tmp_path)
    assert not retire_after_run(store, trigger, status="success", from_review=True, settled_holder=first.holder)
    assert claims.release_claim(trigger.id, holder=first.holder, owner_pid=os.getpid(), base_dir=tmp_path)
    assert not claims.release_claim(trigger.id, holder=first.holder, owner_pid=os.getpid(), base_dir=tmp_path)
    assert retire_after_run(store, trigger, status="success", from_review=True, settled_holder=second.holder)
    assert claims.release_claim(trigger.id, holder=second.holder, owner_pid=os.getpid(), base_dir=tmp_path)
    assert not claims.read_claims(trigger.id, base_dir=tmp_path)


@pytest.mark.asyncio
async def test_actual_parallel_native_inbox_dispatch_releases_own_ticket(tmp_path, monkeypatch):
    import logging
    from gideon.automation.triggers import executor, wakeup
    from gideon.automation.triggers.service import tick, to_iso
    from gideon.engine.automation_routes import AutomationRoutes
    from gideon.engine.gateway import RuntimeCoordinator
    from gideon.engine.session import ConversationDirectory, _Session
    from gideon.core.config.loader import AppConfig

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    trigger = one_shot("clock:queued-parallel", time.time())
    trigger.overlap = "parallel"
    trigger.workflow = {"provider": "notify", "config": {"title_template": "Native queued fire"}}
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    admitted = await tick(store, now=time.time(), persist=True, base_dir=tmp_path)
    assert len(admitted.fires) == 1
    rearmed = store.get(trigger.id).trigger
    rearmed.enabled, rearmed.next_fire_at = True, to_iso(time.time() - 1)
    store.upsert(rearmed)
    second_admission = await tick(store, now=time.time(), persist=True, base_dir=tmp_path)
    assert len(second_admission.fires) == 1
    fires = admitted.fires + second_admission.fires
    tickets = [fire.claim for fire in fires]
    runtime = RuntimeCoordinator(AppConfig())
    state = ConsoleState(None, time.time())
    runtime.dashboard_state = state
    previous_services = get_action_services()
    set_action_services(ActionServices(state, asyncio.create_task))
    directory = ConversationDirectory(AppConfig())
    key = wakeup.session_key_for(trigger.id)
    # The native inbox container does not consult its model adapter for these action fires.
    directory._sessions[key] = _Session(provider=None)
    routes = AutomationRoutes(runtime, logging.getLogger(__name__))
    routes._store = store
    try:
        deliveries = wakeup.dispatch_fires(directory, fires)
        assert all(delivery.disposition == "queued" for delivery in deliveries)
        first = await executor.drain(directory, key, routes._runner, limit=1, base_dir=tmp_path)
        assert len(first.outcomes) == 1
        remaining = claims.read_claims(trigger.id, base_dir=tmp_path)
        assert len(remaining) == 1 and remaining[0].holder == tickets[1].holder
        assert store.get(trigger.id) is not None
        second = await executor.drain(directory, key, routes._runner, limit=1, base_dir=tmp_path)
        assert len(second.outcomes) == 1
        assert not claims.read_claims(trigger.id, base_dir=tmp_path)
        assert store.get(trigger.id) is None
        rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
        assert count == 2 and all(row["status"] == "success" for row in rows)
    finally:
        set_action_services(previous_services)
