from gideon.engine.proactive_plan import plan_commitment
from gideon.engine import proactive_decisions
from datetime import datetime, timezone, timedelta
import asyncio


def test_commitment_decisions_preserve_quiet_and_unavailable_delivery():
    item = {"text": "Review the report", "channel": "dashboard"}
    assert plan_commitment(item, posture="allowed", dashboard_available=True).action == "contribute"
    assert plan_commitment(item, posture="quiet", dashboard_available=True).action == "defer"
    assert plan_commitment(item, posture="allowed", dashboard_available=False).action == "defer"
    assert plan_commitment({"text": " "}, posture="allowed", dashboard_available=True).action == "silence"
    assert plan_commitment({"text": "Prepare work", "channel": "background"}, posture="allowed", dashboard_available=True).action == "defer"


def test_due_commitment_uses_recent_context_and_persists_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    due = "2026-09-25T10:00:00+00:00"
    item = {"text": "Review the report", "channel": "dashboard:session", "due_window": due}
    context = {"recent_sessions": [{"id": "session", "text": "Review the report", "created_at": "2026-09-25T11:00:00+00:00"}], "learned_routines": []}
    plan = plan_commitment(item, posture="allowed", dashboard_available=True, context=context)
    assert plan.action == "silence" and "recent session" in plan.reason
    proactive_decisions.record("topic-1", agent="agent", text=item["text"], action=plan.action,
                               reason=plan.reason, policy="allowed", destination=plan.channel, context=context)
    assert proactive_decisions.get("topic-1")["context"] == context
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    proactive_decisions.dismiss("topic-1", days=7, now=now)
    assert proactive_decisions.suppressed(proactive_decisions.get("topic-1"), now=now + timedelta(days=6))
    assert not proactive_decisions.suppressed(proactive_decisions.get("topic-1"), now=now + timedelta(days=8))
    proactive_decisions.approved_run("topic-1", "run-1")
    proactive_decisions.approved_run("topic-1", "run-2")
    assert proactive_decisions.get("topic-1")["run_id"] == "run-1"


def test_explicit_background_approval_creates_one_persisted_workflow_run(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gideon.automation.workflows import service, store
    from gideon.automation.workflows.controller import EngineServices
    from gideon.automation.workflows.native_defs import register_native_provider
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.interfaces.dashboard.handlers.proactive import api_proactive_reply
    from gideon.interfaces.dashboard.state import ConsoleState

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    register_native_provider()
    proactive_decisions.record("topic-approved", agent="agent", text="Review report",
                               action="notify", reason="due", policy="allowed",
                               destination="dashboard:ui", context={})

    async def journey():
        authored = await service.author_def(
            name="followup-review", root={"kind": "transform", "id": "answer", "config": {"expr": "done"}},
            strict=False,
        )
        assert authored["ok"], authored
        state = ConsoleState(None, 0)
        state.workflows = WorkflowWatchdog(services=EngineServices())
        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/proactive/digest/reply", api_proactive_reply)
        async with TestClient(TestServer(app)) as client:
            payload = {"commitment_key": "topic-approved", "action": "approve_background",
                       "workflow_name": "followup-review", "inputs": {}}
            first = await client.post("/api/proactive/digest/reply", json=payload)
            assert first.status == 200, await first.text()
            body = await first.json()
            run_id = body["run_id"]
            assert store.get(run_id) is not None
            repeated = await client.post("/api/proactive/digest/reply", json=payload)
            assert (await repeated.json())["run_id"] == run_id
            assert proactive_decisions.get("topic-approved")["run_id"] == run_id
            assert len(store.list_runs(workflow_name="followup-review", limit=10)[0]) == 1
            proactive_decisions.record("topic-dismissed", agent="agent", text="Same topic",
                                       action="notify", reason="due", policy="allowed",
                                       destination="dashboard:ui", context={})
            dismissed = await client.post("/api/proactive/digest/reply", json={
                "commitment_key": "topic-dismissed", "action": "dismiss"})
            assert dismissed.status == 200
            refused = await client.post("/api/proactive/digest/reply", json={
                **payload, "commitment_key": "topic-dismissed"})
            assert refused.status == 409
            assert proactive_decisions.get("topic-dismissed")["run_id"] == ""
            await state.workflows.stop()

    asyncio.run(journey())
