import asyncio
import json
import os
import secrets
import tempfile
from pathlib import Path

from aiohttp import web


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-assistant-workflows-") as directory:
        os.environ["GIDEON_HOME"] = directory

        from gideon.automation.workflows.handlers import register_workflow_routes
        from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
        from gideon.automation.workflows.defs import register_provider
        from gideon.automation.workflows import store as workflow_store
        from gideon.automation.workflows import journal as workflow_journal
        from gideon.automation.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun
        from gideon.automation.event_triggers import EventTrigger, EventTriggerStore, MEMORY_UPDATE
        from gideon.automation.loop import files as loop_files
        from gideon.automation.loop import store as loop_store
        from gideon.automation.loop.loop import Loop, LoopStatus
        from gideon.cognition.planning.session import PlanSession, PlanStep
        from gideon.interfaces.dashboard.token_auth import auth_middleware
        from gideon.interfaces.dashboard.state import ConsoleState
        from gideon.interfaces.dashboard.handlers.triggers import register_trigger_routes
        from gideon.interfaces.dashboard.handlers.loop_routes import register_unified_loop_routes
        from gideon.security.auth.modes import AuthConfig, AuthMode

        register_provider(NativeWorkflowDefProvider())
        workflow_name = "assistant-workflow"
        await NativeWorkflowDefProvider().save_def(
            name=workflow_name, description="Native workflow editor fixture", provenance="user",
            root={"kind": "sequence", "id": "main", "children": [
                {"kind": "transform", "id": "seed", "label": "Prepare input", "config": {"expr": {"value": 1}}},
                {"kind": "transform", "id": "finish", "label": "Finish", "config": {"expr": "{{nodes.seed.output.value}}"}, "needs": ["seed"]},
            ]},
        )
        run_id = "assistant-run-fixture"
        workflow_store.create(WorkflowRun(id=run_id, workflow_name=workflow_name,
            status=RunStatus.COMPLETE, owner_username="workflow-owner"))
        workflow_store.write_spec(run_id, {"root": {"kind": "sequence", "id": "main", "children": [
            {"kind": "transform", "id": "finish", "label": "Finish", "config": {"expr": {"value": 1}}},
        ]}})
        workflow_store.write_state(run_id, {"root.children[0]": NodeInstance(
            path="root.children[0]", state=InstanceState.DONE, attempt=1)})
        journal = workflow_journal.Journal(run_id)
        for problem in ("review-success", "review-ambiguous"):
            journal.write(workflow_journal.REVIEW_FINDING, node_id="reviewer", template=workflow_name,
                severity="Minor", location="src/app.ts:1", problem=problem,
                why="Native review fixture", recommended_fix="Inspect the exact finding",
                status="Open", auto_fixable=False, line_text="", origin_run_id=run_id,
                origin_node_id="reviewer", origin_session_key="run-source")
        trigger_id = "event:assistant-event-fixture"
        EventTriggerStore(Path(directory) / "event_triggers.json").upsert(EventTrigger(
            id="assistant-event-fixture", pattern=MEMORY_UPDATE, source="memory",
            action_provider="notify", action_config={}, max_fires=8,
        ))
        loop = loop_store.create(Loop(
            id="a5500001", name="Assistant loop fixture", kind="general",
            task="Review the fixture run and report its native state clearly.",
            status=LoopStatus.REVIEW.value, plan=[{"title": "Review current state"}],
        ))
        loop_files.write_plan_session(PlanSession(project_id=loop.id, steps=[PlanStep(
            id="review-state", kind="review", title="Review current state",
            objective="Confirm the native run state.", status="awaiting_review",
            artifact={"summary": "Native loop review fixture", "source_run_id": run_id},
        )]))
        loop_files.write_question(loop.id, "Which follow-up should happen next?", why="The native loop is waiting for an owner decision.")
        loop_dir = loop_files.loop_dir(loop.id)
        if loop_dir:
            (loop_dir / "REPORT.md").write_text("Native fixture report linked to assistant-run-fixture.", encoding="utf-8")
        credential = secrets.token_urlsafe(24)
        os.environ["GIDEON_WORKFLOW_TEST_API_KEY"] = credential
        held_detail = False
        detail_started = asyncio.Event()
        release_detail = asyncio.Event()
        save_writes = 0
        triage_attempts = 0
        drop_next_triage_response = False
        hold_next_native_write = False
        native_write_started = asyncio.Event()
        release_native_write = asyncio.Event()
        hold_trigger_reads = False
        trigger_read_started = asyncio.Event()
        release_trigger_reads = asyncio.Event()
        hold_loop_preflight = False
        loop_preflight_started = asyncio.Event()
        release_loop_preflight = asyncio.Event()
        loop_create_attempts = 0

        async def controls(request: web.Request) -> web.Response:
            nonlocal held_detail, save_writes, drop_next_triage_response, hold_next_native_write, hold_trigger_reads, hold_loop_preflight
            if request.path == "/__test/hold-next-native-write" and request.method == "POST":
                hold_next_native_write = True
                native_write_started.clear()
                release_native_write.clear()
                return web.json_response({"armed": True})
            if request.path == "/__test/native-write-started" and request.method == "GET":
                try:
                    await asyncio.wait_for(native_write_started.wait(), timeout=5)
                    return web.json_response({"started": True})
                except asyncio.TimeoutError:
                    return web.json_response({"started": False}, status=408)
            if request.path == "/__test/release-native-write" and request.method == "POST":
                release_native_write.set()
                return web.json_response({"released": True})
            if request.path == "/__test/hold-trigger-reads" and request.method == "POST":
                hold_trigger_reads = True
                trigger_read_started.clear()
                release_trigger_reads.clear()
                return web.json_response({"armed": True})
            if request.path == "/__test/trigger-read-started" and request.method == "GET":
                try:
                    await asyncio.wait_for(trigger_read_started.wait(), timeout=5)
                    return web.json_response({"started": True})
                except asyncio.TimeoutError:
                    return web.json_response({"started": False}, status=408)
            if request.path == "/__test/release-trigger-reads" and request.method == "POST":
                hold_trigger_reads = False
                release_trigger_reads.set()
                return web.json_response({"released": True})
            if request.path == "/__test/hold-loop-preflight" and request.method == "POST":
                hold_loop_preflight = True
                loop_preflight_started.clear()
                release_loop_preflight.clear()
                return web.json_response({"armed": True})
            if request.path == "/__test/loop-preflight-started" and request.method == "GET":
                try:
                    await asyncio.wait_for(loop_preflight_started.wait(), timeout=5)
                    return web.json_response({"started": True})
                except asyncio.TimeoutError:
                    return web.json_response({"started": False}, status=408)
            if request.path == "/__test/release-loop-preflight" and request.method == "POST":
                hold_loop_preflight = False
                release_loop_preflight.set()
                return web.json_response({"released": True})
            if request.path == "/__test/arm" and request.method == "POST":
                held_detail = True
                save_writes = 0
                detail_started.clear()
                release_detail.clear()
                return web.json_response({"armed": True})
            if request.path == "/__test/wait" and request.method == "GET":
                try:
                    await asyncio.wait_for(detail_started.wait(), timeout=5)
                    return web.json_response({"started": True})
                except asyncio.TimeoutError:
                    return web.json_response({"started": False}, status=408)
            if request.path == "/__test/release" and request.method == "POST":
                held_detail = False
                release_detail.set()
                return web.json_response({"released": True})
            if request.path == "/__test/stats" and request.method == "GET":
                calibrations = workflow_journal.ledger(run_id, kinds={workflow_journal.JUDGE_DIVERGENCE})
                return web.json_response({"save_writes": save_writes, "triage_attempts": triage_attempts,
                    "calibration_count": len(calibrations), "loop_create_attempts": loop_create_attempts})
            if request.path == "/__test/drop-next-triage" and request.method == "POST":
                drop_next_triage_response = True
                return web.json_response({"armed": True})
            return web.json_response({"error": "unknown test control"}, status=404)

        @web.middleware
        async def test_control(request: web.Request, handler):
            nonlocal held_detail, save_writes, triage_attempts, drop_next_triage_response, hold_next_native_write, hold_trigger_reads, hold_loop_preflight, loop_create_attempts
            if request.path.startswith("/__test/"):
                return await controls(request)
            if hold_trigger_reads and request.method == "GET" and request.path == "/api/triggers":
                trigger_read_started.set()
                await release_trigger_reads.wait()
            if request.method == "POST" and request.path == "/api/loops":
                loop_create_attempts += 1
            if hold_loop_preflight and request.method == "POST" and request.path == "/api/loops/validate":
                response = await handler(request)
                hold_loop_preflight = False
                loop_preflight_started.set()
                await release_loop_preflight.wait()
                return response
            if request.method == "POST" and request.path == "/api/workflows":
                body = await request.json()
                if body.get("save") is True:
                    save_writes += 1
            if request.method == "GET" and request.path.startswith("/api/workflows/") and request.path != "/api/workflows/runs" and held_detail:
                held_detail = False
                detail_started.set()
                await release_detail.wait()
            if request.method == "POST" and request.path == f"/api/workflows/runs/{run_id}/review/triage":
                triage_attempts += 1
                response = await handler(request)
                if drop_next_triage_response:
                    drop_next_triage_response = False
                    return web.json_response({"error": "native triage response was disconnected"}, status=503)
                return response
            if hold_next_native_write and request.method in {"POST", "PUT", "PATCH", "DELETE"} \
                    and (request.path.startswith("/api/triggers/") or request.path.startswith("/api/loops/")):
                response = await handler(request)
                hold_next_native_write = False
                native_write_started.set()
                await release_native_write.wait()
                return web.json_response({"error": "native write acknowledgement was disconnected"}, status=503)
            return await handler(request)

        app = web.Application(middlewares=[test_control, auth_middleware(
            AuthConfig(mode=AuthMode.API_KEY, api_key_env="GIDEON_WORKFLOW_TEST_API_KEY"))])
        app["state"] = ConsoleState(None, asyncio.get_running_loop().time())
        register_workflow_routes(app)
        register_trigger_routes(app)
        register_unified_loop_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({"port": port, "credential": credential, "workflow_name": workflow_name, "run_id": run_id,
            "trigger_id": trigger_id, "loop_id": loop.id}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
