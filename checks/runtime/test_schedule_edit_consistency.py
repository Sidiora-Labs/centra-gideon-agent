"""Native schedule mutation, served revisions, dispatch and investigation checks."""

import asyncio
import logging
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers import tools
from gideon.automation.triggers.arm import arm
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.service import tick, to_epoch, to_iso
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import AppConfig
from gideon.engine.automation_routes import AutomationRoutes
from gideon.engine.gateway import RuntimeCoordinator
from gideon.integrations.action_providers.services import (
    ActionServices,
    get_action_services,
    set_action_services,
)
from gideon.interfaces.dashboard.handlers.investigate import register_investigate_routes
from gideon.interfaces.dashboard.handlers.triggers import register_trigger_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


def clock(identity, spec):
    trigger = Trigger(
        id=identity,
        name="Morning schedule",
        kind="clock",
        spec=spec,
        capabilities={"providers": ["notify"]},
        delivery="none",
        workflow={
            "provider": "notify",
            "config": {"title_template": "Scheduled action"},
        },
    )
    trigger.next_fire_at = arm(trigger)
    return trigger


@pytest.mark.parametrize("cadence", ["cron", "interval", "at"])
def test_real_store_chat_edits_preserve_slots_and_rearm_changes(
    tmp_path, monkeypatch, cadence
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    now = time.time()
    before, after = {
        "cron": (
            {"kind": "cron", "expr": "0 9 * * *"},
            {"kind": "cron", "expr": "30 7 * * *"},
        ),
        "interval": (
            {"kind": "interval", "interval_secs": 60},
            {"kind": "interval", "interval_secs": 120},
        ),
        "at": ({"kind": "at", "at": now + 120}, {"kind": "at", "at": now + 240}),
    }[cadence]
    before, after = {"created_at": now, **before}, {"created_at": now, **after}
    store = TriggerStore(base_dir=tmp_path)
    trigger = clock(f"clock:edit:{cadence}", before)
    store.upsert(trigger)
    slot = trigger.next_fire_at
    assert tools.update(store, trigger_id=trigger.id, patch={"name": "Renamed"}).ok
    assert store.get(trigger.id).trigger.next_fire_at == slot
    unchanged = {**before, "timezone": "", "skip_dates": [], "strict": True}
    assert tools.update(store, trigger_id=trigger.id, patch={"spec": unchanged}).ok
    assert store.get(trigger.id).trigger.next_fire_at == slot
    assert tools.update(store, trigger_id=trigger.id, patch={"spec": after}).ok
    edited = store.get(trigger.id).trigger
    assert edited.next_fire_at != slot and edited.next_fire_at == arm(edited)
    edited.last_run_id, edited.run_count = "runtime-stamp", 4
    store.upsert(edited)
    assert store.get(trigger.id).trigger.next_fire_at == edited.next_fire_at
    edited.enabled, edited.next_fire_at = False, ""
    store.upsert(edited)
    assert tools.set_paused(store, trigger_id=trigger.id, paused=False).ok
    resumed = store.get(trigger.id).trigger
    assert resumed.enabled and resumed.next_fire_at == arm(resumed)


@pytest.mark.asyncio
async def test_actual_http_revision_edit_dispatch_and_deleted_run_investigation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.handlers.triggers.config_dir", lambda: tmp_path
    )
    store = TriggerStore(base_dir=tmp_path)
    trigger = clock(
        "system:triage:colon:clock",
        {"kind": "interval", "interval_secs": 60, "created_at": time.time()},
    )
    store.upsert(trigger)
    initial_slot = trigger.next_fire_at
    state = ConsoleState(None, time.time())
    runtime = RuntimeCoordinator(AppConfig())
    runtime.dashboard_state = state
    previous_services = get_action_services()
    set_action_services(ActionServices(state, asyncio.create_task))
    routes = AutomationRoutes(runtime, logging.getLogger(__name__))
    routes._store = store
    app = web.Application(middlewares=[token_auth_middleware(port=19423)])
    app["state"] = state
    register_trigger_routes(app)
    register_investigate_routes(app)
    cookies = {"gideon_token_19423": generate_token("schedule-editor", kind="desktop")}
    try:
        async with TestClient(TestServer(app)) as client:
            listing = await client.get("/api/triggers", cookies=cookies)
            served = next(
                row
                for row in (await listing.json())["triggers"]
                if row["id"] == f"schedule:{trigger.id}"
            )
            revision = served["document_revision"]
            renamed = await client.put(
                f"/api/triggers/schedule:{trigger.id}",
                json={"name": "HTTP renamed", "timezone": "", "skip_dates": []},
                headers={"If-Match": revision},
                cookies=cookies,
            )
            assert renamed.status == 200, await renamed.text()
            assert store.get(trigger.id).trigger.next_fire_at == initial_slot
            stale = await client.put(
                f"/api/triggers/schedule:{trigger.id}",
                json={"every": 120},
                headers={"If-Match": revision},
                cookies=cookies,
            )
            assert stale.status == 409
            listing = await client.get("/api/triggers", cookies=cookies)
            served = next(
                row
                for row in (await listing.json())["triggers"]
                if row["id"] == f"schedule:{trigger.id}"
            )
            changed = await client.put(
                f"/api/triggers/schedule:{trigger.id}",
                json={"every": 120},
                headers={"If-Match": served["document_revision"]},
                cookies=cookies,
            )
            assert changed.status == 200, await changed.text()
            edited = store.get(trigger.id).trigger
            assert edited.next_fire_at != initial_slot
            assert abs(to_epoch(edited.next_fire_at) - to_epoch(arm(edited))) < 2
            admitted = await tick(
                store,
                now=to_epoch(edited.next_fire_at) + 1,
                persist=True,
                base_dir=tmp_path,
            )
            assert len(admitted.fires) == 1
            await routes._runner(admitted.fires[0].to_dict())
            rows, count = await ExecutionJournal(tmp_path).list_for_job(trigger.id)
            assert count == 1 and rows[0]["status"] == "success"
            exact = f"{trigger.id}:{rows[0]['run_id']}"
            investigated = await client.post(
                "/api/investigate",
                json={"kind": "schedule_run", "id": exact},
                cookies=cookies,
            )
            assert investigated.status == 200, await investigated.text()
            context = (await investigated.json())["context"]
            assert (
                rows[0]["run_id"] in context["snapshot"]
                and "HTTP renamed" in context["title"]
            )
            deleted = await client.delete(
                f"/api/triggers/store:{trigger.id}", cookies=cookies
            )
            assert deleted.status == 200
            latest = await client.post(
                "/api/investigate",
                json={"kind": "schedule_run", "id": trigger.id},
                cookies=cookies,
            )
            assert latest.status == 200, await latest.text()
            assert rows[0]["run_id"] in (await latest.json())["context"]["snapshot"]
            exact_deleted = await client.post(
                "/api/investigate",
                json={"kind": "schedule_run", "id": exact},
                cookies=cookies,
            )
            assert exact_deleted.status == 200
    finally:
        set_action_services(previous_services)
