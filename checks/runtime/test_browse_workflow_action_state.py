"""Browser admission and workflow actions through real stores, grants and sockets."""

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.automation.workflows import defs, effects, overlap, store
from gideon.automation.workflows.models import RunStatus, WorkflowRun
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.agents.native.approval import ApprovalGate
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers import browse_provider as browse
from gideon.integrations.action_providers import services
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.run_workflow_provider import (
    RunWorkflowActionProvider,
)
from gideon.integrations.browse import grant, handoff, killswitch, target
from gideon.integrations.browse.cdp import GatedCdpSession
from gideon.integrations.browse.loop import BrowseLoopResult, BrowseStep
from gideon.integrations.browse.page import CdpPageDriver
from gideon.integrations.inbox_providers import native_source
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations.durability.state_history import (
    SURFACE_BACKGROUND,
    writing_surface,
)
from gideon.security import trust_mode
from gideon.security.guardrails import budgets, incident

CTX = ActionContext("schedule", "trigger-local")
PAGE = "https://browse-action.example.test/content"


@pytest.fixture(autouse=True)
def action_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [], "browse": {"user_browser_enabled": True}})
    )
    monkeypatch.setattr(defs, "_providers", {})
    monkeypatch.setattr(effects, "START_DEDUPE", effects.CallerDedupe())
    monkeypatch.setattr(services, "_services", None)
    monkeypatch.setattr(grant, "_gate", ApprovalGate())
    monkeypatch.setattr(grant, "_pending", {})
    monkeypatch.setattr(target, "_session", None)
    monkeypatch.setattr(native_source, "_dashboard_state", None)
    monkeypatch.setattr(budgets, "_METER", budgets.SpendMeter(config_dir=tmp_path))
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    killswitch.reset_browse_kill_mirror()
    yield tmp_path
    killswitch.reset_browse_kill_mirror()


def audit_rows(home):
    path = home / "security_events.jsonl"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


@pytest.mark.asyncio
async def test_actual_browse_guards_refuse_without_opening_any_browser():
    provider = browse.BrowseActionProvider()
    cfg = {"goal": "read", "start_url": PAGE, "target": "user_browser"}
    disconnected = await provider.execute(cfg, CTX)
    assert disconnected.success and disconnected.outcome == "skip"
    assert disconnected.agent_error.code == "ERR_BROWSE_USER_BROWSER_DISCONNECTED"
    target.register_connector(device_id="local", cdp_url="invalid-websocket-scheme")
    with writing_surface(SURFACE_BACKGROUND):
        background = await provider.execute(cfg, CTX)
    assert background.agent_error.code == "ERR_BROWSE_TARGET_UNATTENDED"
    killswitch.engage("operator stop")
    stopped = await provider.execute(cfg, CTX)
    assert stopped.agent_error.code == "ERR_BROWSE_KILLED"
    incident.activate("global stop")
    incident_result = await provider.execute(cfg, CTX)
    assert incident_result.agent_error.code == "ERR_BROWSE_INCIDENT_ACTIVE"
    unknown = await provider.execute({**cfg, "target": "unknown"}, CTX)
    assert unknown.agent_error.code == "ERR_BROWSE_TARGET_UNKNOWN"
    assert grant.pending_grants() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approve", "url", "code", "outcome"),
    [
        (False, PAGE, "ERR_BROWSE_GRANT_DENIED", ""),
        (True, PAGE, "ERR_BROWSE_CONNECT_FAILED", ""),
        (True, "https://browse-action.example.test/login", "", "needs_input"),
    ],
)
async def test_real_task_grant_is_settled_on_refusal_connect_failure_and_login_park(
    action_home, approve, url, code, outcome
):
    target.register_connector(device_id="local", cdp_url="invalid-websocket-scheme")
    task = asyncio.create_task(
        browse.BrowseActionProvider().execute(
            {"goal": "read", "start_url": url, "target": "user_browser"}, CTX
        )
    )
    try:
        async with asyncio.timeout(5):
            while not grant.pending_grants():
                await asyncio.sleep(0)
        pending = grant.pending_grants()[0]
        assert pending["scope"] == ["browse-action.example.test"]
        decision = grant.approve_grant if approve else grant.reject_grant
        assert decision(str(pending["request_id"]))
        result = await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert result.outcome == outcome
    assert (result.agent_error.code if result.agent_error else "") == code
    rows = [
        row
        for row in audit_rows(action_home)
        if row["operation"] in ("browser_grant", "browser_revoked")
    ]
    assert [row["operation"] for row in rows] == (
        ["browser_grant", "browser_revoked"] if approve else ["browser_grant"]
    )
    if approve:
        assert "reason=run_ended" in rows[-1]["resources"]
    assert grant.pending_grants() == []
    assert grant.grant_gate()._requests == {}


@pytest.mark.asyncio
async def test_cancellation_while_awaiting_grant_removes_pending_state():
    target.register_connector(device_id="local", cdp_url="invalid-websocket-scheme")
    pending = asyncio.create_task(
        browse.BrowseActionProvider().execute(
            {"goal": "read", "start_url": PAGE, "target": "user_browser"}, CTX
        )
    )
    try:
        async with asyncio.timeout(5):
            while not grant.pending_grants():
                await asyncio.sleep(0)
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert grant.pending_grants() == [] and grant.grant_gate()._requests == {}


@pytest.mark.asyncio
async def test_browser_connection_owner_closes_a_real_websocket(action_home):
    connected = asyncio.Event()
    disconnected = asyncio.Event()

    async def websocket(request):
        channel = web.WebSocketResponse()
        await channel.prepare(request)
        connected.set()
        try:
            async for message in channel:
                pass
        finally:
            disconnected.set()
        return channel

    app = web.Application()
    app.router.add_get("/page", websocket)
    async with TestServer(app, host="127.0.0.1") as server:
        url = str(server.make_url("/page")).replace("http://", "ws://", 1)
        session, page, closer = await browse.BrowseActionProvider()._open(
            {"screenshot_dir": str(action_home / "shots")}, CTX, cdp_url=url
        )
        assert isinstance(session, GatedCdpSession) and isinstance(page, CdpPageDriver)
        await asyncio.wait_for(connected.wait(), 2)
        owner = browse._BrowserOwnership(closer=closer)
        await owner.close()
        await owner.close()
        await asyncio.wait_for(disconnected.wait(), 2)


@pytest.mark.parametrize(
    "reason",
    [
        browse.PARK_STEP_EXHAUSTED,
        browse.PARK_BUDGET_EXHAUSTED,
        browse.PARK_KILLED,
        browse.PARK_TAB_CLOSED,
        "other",
    ],
)
def test_typed_browse_park_retains_work_and_human_sentence(reason):
    step = BrowseStep(1, PAGE, "NOTES retained", True, note="retained")
    result = BrowseLoopResult(
        ok=True,
        goal="read",
        final_url=PAGE,
        steps=(step,),
        notes=("retained",),
        parked=True,
        park_reason=reason,
    )
    projected = browse.BrowseActionProvider()._to_result(
        result, started=time.monotonic(), ctx=CTX
    )
    assert projected.success and projected.outcome == "needs_input"
    assert json.loads(projected.stdout)["notes"] == ["retained"]
    assert "1 note(s) kept" in projected.stderr and PAGE in projected.stderr


def test_completed_projection_records_real_session_but_failure_does_not():
    provider = browse.BrowseActionProvider()
    failed = BrowseLoopResult(
        ok=False, goal="read", error="navigation denied", notes=("kept",)
    )
    result = provider._to_result(
        failed, started=time.monotonic(), ctx=CTX, start_url=PAGE
    )
    assert not result.success and result.agent_error.code == "ERR_BROWSE_FAILED"
    assert json.loads(result.stdout)["notes"] == ["kept"]
    assert handoff.session_state(PAGE) == handoff.SESSION_ABSENT
    complete = BrowseLoopResult(ok=True, goal="read", final_url=PAGE)
    provider._to_result(
        complete,
        started=time.monotonic(),
        ctx=CTX,
        start_url=PAGE,
        session_before=handoff.SESSION_ABSENT,
    )
    assert handoff.session_state(PAGE) == handoff.SESSION_FRESH


def test_real_day_and_run_spend_are_consulted_in_order(action_home):
    (action_home / "config.json").write_text(
        json.dumps(
            {"providers": [], "guardrails": {"budgets": {"max_tokens_per_day": 100}}}
        )
    )
    meter = budgets.get_meter()
    meter.charge(20, 0, run_key="local-run")
    key = budgets.set_current_run_key("local-run")
    bound = budgets.set_current_run_budget(budgets.Budget(max_tokens=10))
    try:
        verdict, reason = browse._budget_check()
        assert verdict == "exceeded" and "run" in reason
        meter.charge(100, 0)
        verdict, reason = browse._budget_check()
        assert verdict == "exceeded" and "day" in reason
    finally:
        budgets.reset_current_run_key(key)
        budgets.reset_current_run_budget(bound)


async def saved_definition(policy="skip"):
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    return await provider.save_def(
        name="local-flow",
        on_overlap=policy,
        root={"kind": "transform", "id": "local", "config": {"expr": "done"}},
    )


def actual_workflow_services():
    sessions = ConversationDirectory(AppConfig.load())
    state = ConsoleState(sessions=sessions, start_time=0)
    supervisor = WorkflowWatchdog()
    services.set_action_services(
        services.ActionServices(state, asyncio.create_task, workflows=supervisor)
    )
    return supervisor


@pytest.mark.asyncio
async def test_real_workflow_launch_pins_definition_and_reuses_caller_identity():
    await saved_definition()
    definition = await saved_definition()
    supervisor = actual_workflow_services()
    try:
        config = {
            "workflow": "local-flow",
            "idempotency_key": "local-start",
            "inputs": {"x": 1},
            "project_id": "project",
            "mode": "blocking",
        }
        first = await RunWorkflowActionProvider().execute(config, CTX)
        assert first.success and first.outcome == "launched"
        run_id = json.loads(first.stdout)["run_id"]
        run = store.get(run_id)
        assert run.spec_version == definition.version == 2
        assert run.origin.trigger_id == "trigger-local" and run.inputs == {"x": 1}
        assert run.project_id == "project" and run.mode == "blocking"
        duplicate = await RunWorkflowActionProvider().execute(config, CTX)
        assert json.loads(duplicate.stdout) == {"run_id": run_id, "deduped": True}
        controller = supervisor.controller(run_id)
        assert await controller.run_to_completion(timeout=10) == RunStatus.COMPLETE
        assert store.get(run_id).status == RunStatus.COMPLETE
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_real_queue_preview_and_cap_never_start_unrequested_runs():
    definition = await saved_definition("queue")
    prior = store.create(
        WorkflowRun(id="", workflow_name="local-flow", status=RunStatus.RUNNING)
    )
    store.write_spec(prior.id, definition.to_dict())
    action = RunWorkflowActionProvider()
    preview = await action.execute({"workflow": "local-flow", "dry_run": True}, CTX)
    assert json.loads(preview.stdout)["would"] == "queue"
    assert overlap.queued_runs("local-flow") == []
    queued = await action.execute(
        {"workflow": "local-flow", "idempotency_key": "queued-start"}, CTX
    )
    body = json.loads(queued.stdout)
    assert queued.outcome == "queued" and body["behind"] == [prior.id]
    record = store.get(body["run_id"])
    assert record.status == RunStatus.DRAFT and overlap.is_queued(record)
    assert store.read_spec(record.id)["version"] == definition.version
    dropped = await action.execute({"workflow": "local-flow"}, CTX)
    assert (
        dropped.outcome == "skip"
        and json.loads(dropped.stdout)["reason"] == "queue_full"
    )
    duplicate = await action.execute(
        {"workflow": "local-flow", "idempotency_key": "queued-start"}, CTX
    )
    assert json.loads(duplicate.stdout) == {"run_id": record.id, "deduped": True}
    assert len(overlap.queued_runs("local-flow")) == 1
    assert not store.cancel_requested(prior.id)


@pytest.mark.asyncio
async def test_real_cancel_previous_requests_cancel_and_launches_new_controller():
    definition = await saved_definition("cancel_previous")
    prior = store.create(
        WorkflowRun(id="", workflow_name="local-flow", status=RunStatus.RUNNING)
    )
    store.write_spec(prior.id, definition.to_dict())
    supervisor = actual_workflow_services()
    try:
        result = await RunWorkflowActionProvider().execute(
            {"workflow": "local-flow"}, CTX
        )
        assert result.outcome == "launched" and store.cancel_requested(prior.id)
        run_id = json.loads(result.stdout)["run_id"]
        assert (
            await supervisor.controller(run_id).run_to_completion(timeout=10)
            == RunStatus.COMPLETE
        )
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_missing_supervisor_reports_persisted_but_unstarted_run():
    definition = await saved_definition()
    result = await RunWorkflowActionProvider().execute({"workflow": "local-flow"}, CTX)
    assert not result.success and "supervisor" in result.error
    body = json.loads(result.stdout)
    assert body["started"] is False
    run = store.get(body["run_id"])
    assert run.status == RunStatus.DRAFT and run.spec_version == definition.version
    assert store.read_spec(run.id)["name"] == "local-flow"
