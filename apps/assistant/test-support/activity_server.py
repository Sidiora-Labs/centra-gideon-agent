import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web

from gideon.automation.event_triggers import EventTrigger, EventTriggerStore
from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.automation.workflows import store as workflow_store
from gideon.automation.workflows.handlers import api_run_status, api_runs_list
from gideon.automation.workflows.models import RunStatus, WorkflowRun
from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.hooks import ScriptHookStore
from gideon.engine.tasks import registry as task_registry
from gideon.engine.tasks.handlers import api_tasks_get, api_tasks_list
from gideon.integrations.inbox import InboxItem, InboxStore, ItemKind, emit_attention_item
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.messaging import api_notifications
from gideon.interfaces.dashboard.handlers.sessions import api_approval_resolve, api_approvals
from gideon.interfaces.dashboard.handlers.triggers import (
    api_trigger_history_all,
    api_trigger_history_detail,
    api_triggers,
)
from gideon.interfaces.dashboard.handlers_inbox import api_inbox_list, api_inbox_open_items
from gideon.interfaces.dashboard.handlers_inbox import api_inbox_note_create, _redact_item
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.ws import api_ws
from gideon.security.auth import credentials
from gideon.workspace.artifacts import registry as artifact_registry
from gideon.workspace.artifacts.handlers import api_artifact_detail, api_artifacts_list


PASSWORD = "correct-horse-battery-staple"


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-activity-") as temporary_home:
        home = Path(temporary_home)
        os.environ["GIDEON_HOME"] = str(home)
        (home / "config.json").write_text(json.dumps({
            "auth": {"login_enabled": True}, "dashboard": {"username": "owner-a"},
        }), encoding="utf-8")
        credentials.set_password("owner-a", PASSWORD)
        token_auth.use_persistent_secret()
        token_auth.revoke_all_sessions()

        config = AppConfig()
        state = ConsoleState(
            sessions=ConversationDirectory(config), start_time=0.0,
            conversation_log=ConversationLog(base_dir=home / "history"), owner_id="owner-a",
        )
        inbox = InboxStore(home / "inbox_items.json")
        inbox.add(InboxItem(
            id="inbox-1", channel="assistant", channel_name="Assistant", thread_ts=None,
            message="Review result", sender_id="gideon", sender_name="Gideon",
            item_kind="agent_request", refs={"approval": "approval-1", "task_id": "task-related", "session": "chat-1"},
            created_at=time.time(), owner="owner-a",
        ))
        inbox.save()
        state._inbox_store = inbox
        approval_requests = {}
        approval_results = {}

        async def start_approval(approval_id: str, tool: str, tool_input: str, session: str) -> None:
            task = asyncio.create_task(state.request_approval(
                approval_id, "dashboard", tool, tool_input=tool_input,
                tool_purpose=f"Review the native {tool} request", session=session,
            ))
            approval_requests[approval_id] = task
            task.add_done_callback(lambda completed: approval_results.setdefault(approval_id, []).append(
                completed.result() if not completed.cancelled() else False))
            for _ in range(200):
                if approval_id in state._pending_approvals:
                    return
                await asyncio.sleep(0.005)
            raise RuntimeError(f"Approval {approval_id} did not become pending")

        await start_approval("approval-1", "send_message",
            '{"to":"room-a","text":"first exact request"}', "chat-1")
        state._notification_log = [{
            "kind": "system", "title": "Result ready", "body": "Open the result",
            "ts": str(time.time()), "acked": False,
        }, {
            "id": "notification-1", "kind": "system", "title": "Receipt available",
            "body": "Gideon recorded an update", "ts": str(time.time()), "acked": False,
        }]

        for index in range(22):
            await task_registry.create_task(title=f"Native task {index}", status="open", author="owner-a")
        await asyncio.sleep(1.05)
        await task_registry.create_task(title="Another owner's task", status="open", author="owner-b")
        for index in range(21):
            workflow_store.create(WorkflowRun(
                id=f"workflow-page-{index}", workflow_name="native-workflow", status=RunStatus.NEEDS_INPUT,
                owner_username="owner-a", created_at=f"2020-01-01T00:00:{index:02d}Z",
            ))
        workflow_store.create(WorkflowRun(
            id="workflow-other", workflow_name="native-workflow", status=RunStatus.NEEDS_INPUT,
            owner_username="owner-b", created_at="2999-01-01T00:00:00Z",
        ))
        workflow_store.create(WorkflowRun(
            id="workflow-1", workflow_name="native-workflow", status=RunStatus.NEEDS_INPUT,
            owner_username="owner-a",
        ))
        await ExecutionJournal(home).append(ExecutionRecord(
            run_id="trigger-1", job_id="schedule-1", started_at=time.time(),
            finished_at=time.time(), status="success", summary="Scheduled work ran",
        ))
        artifact_registry.get_provider().create(
            name="Native result", content="Result", kind="markdown", source="chat", slug="native-result",
        )

        settings = {"delay": 0.0, "delay_path": "/api/tasks", "workflow_failures": 0,
            "delay_remaining": None, "delay_entered": 0, "delay_completed": 0, "delay_release": None,
            "replace_on_success": False, "ws_offline": False}

        @web.middleware
        async def delay_reads(request: web.Request, handler):
            should_delay = request.path == settings["delay_path"] and settings["delay"] and (
                settings["delay_remaining"] is None or settings["delay_remaining"] > 0)
            if should_delay:
                if settings["delay_remaining"] is not None:
                    settings["delay_remaining"] -= 1
                settings["delay_entered"] += 1
                try:
                    release = settings["delay_release"]
                    if release is not None:
                        await release.wait()
                    else:
                        await asyncio.sleep(settings["delay"])
                except asyncio.CancelledError:
                    raise
            try:
                response = await handler(request)
            except Exception:
                if request.path == "/api/workflows/runs":
                    settings["workflow_failures"] += 1
                raise
            if request.path == "/api/workflows/runs" and response.status >= 400:
                settings["workflow_failures"] += 1
            if should_delay:
                settings["delay_completed"] += 1
            return response

        app = web.Application(middlewares=[delay_reads, token_auth.token_auth_middleware(port=10000)])
        app["port"] = 10000
        app["allowed_origins"] = {origin}
        app["state"] = state
        app.router.add_get("/api/auth/status", auth.api_login_status)
        app.router.add_get("/api/auth/session", auth.api_auth_session)
        app.router.add_post("/api/auth/login", auth.api_auth_login)
        app.router.add_post("/api/auth/logout", auth.api_auth_logout)
        app.router.add_get("/api/tasks", api_tasks_list)
        app.router.add_get("/api/tasks/{task_id}", api_tasks_get)
        app.router.add_get("/api/workflows/runs", api_runs_list)
        app.router.add_get("/api/workflows/runs/{run_id}", api_run_status)
        app.router.add_get("/api/triggers/history", api_trigger_history_all)
        app.router.add_get("/api/triggers/{id}/history/{run_id}", api_trigger_history_detail)
        app.router.add_get("/api/triggers", api_triggers)
        app.router.add_get("/api/inbox", api_inbox_list)
        app.router.add_get("/api/inbox/open", api_inbox_open_items)
        app.router.add_post("/api/inbox/notes", api_inbox_note_create)
        app.router.add_get("/api/approvals", api_approvals)

        async def resolve_approval(request: web.Request) -> web.Response:
            response = await api_approval_resolve(request)
            if response.status < 300 and settings["replace_on_success"]:
                settings["replace_on_success"] = False
                previous = approval_requests.get(request.match_info["id"])
                if previous is not None and not previous.done():
                    await previous
                await start_approval(request.match_info["id"], "write_file",
                    '{"path":"/workspace/third.txt"}', "chat-3")
            return response

        app.router.add_post("/api/approvals/{id}/{action}", resolve_approval)
        app.router.add_get("/api/notifications", api_notifications)
        app.router.add_get("/api/artifacts", api_artifacts_list)
        app.router.add_get("/api/artifacts/{slug}", api_artifact_detail)

        async def activity_websocket(request: web.Request) -> web.StreamResponse:
            if settings["ws_offline"]:
                return web.json_response({"error": "temporarily unavailable"}, status=503)
            return await api_ws(request)

        app.router.add_get("/api/ws", activity_websocket)

        async def control_delay(request: web.Request) -> web.Response:
            body = await request.json()
            settings["delay"] = float(body["seconds"])
            settings["delay_path"] = body.get("path", "/api/tasks")
            settings["delay_remaining"] = body.get("requests")
            settings["delay_entered"] = 0
            settings["delay_completed"] = 0
            settings["delay_release"] = asyncio.Event() if body.get("hold") else None
            return web.json_response({"ok": True})

        async def control_delay_state(request: web.Request) -> web.Response:
            return web.json_response({"entered": settings["delay_entered"],
                "completed": settings["delay_completed"], "remaining": settings["delay_remaining"]})

        async def control_delay_release(request: web.Request) -> web.Response:
            release = settings["delay_release"]
            if release is not None:
                release.set()
            return web.json_response({"ok": True})

        async def control_empty_inbox(request: web.Request) -> web.Response:
            inbox.items.clear()
            inbox.save()
            return web.json_response({"ok": True})

        async def control_replace_approval(request: web.Request) -> web.Response:
            existing = approval_requests.get("approval-1")
            if existing is not None and not existing.done():
                state.resolve_approval("approval-1", False)
                await existing
            await start_approval("approval-1", "delete_file",
                '{"path":"/workspace/second.txt"}', "chat-2")
            return web.json_response({"revision": state._pending_approvals["approval-1"]["revision"]})

        async def control_replace_on_success(request: web.Request) -> web.Response:
            settings["replace_on_success"] = True
            return web.json_response({"ok": True})

        async def control_approval_state(request: web.Request) -> web.Response:
            return web.json_response({"pending": sorted(state._pending_approvals), "results": approval_results})

        async def control_failures(request: web.Request) -> web.Response:
            return web.json_response({"workflow": settings["workflow_failures"]})

        async def control_task_ownership(request: web.Request) -> web.Response:
            owner = request.query.get("owner", "owner-a")
            rows, total = await task_registry.list_all_tasks(limit=500, offset=0)
            owned, owned_total = await task_registry.list_all_tasks(owner=owner, limit=500, offset=0)
            return web.json_response({"total": total, "owner": owner,
                "owned_total": owned_total, "owned_ids": [row.id for row in owned],
                "registry_file": str(Path(task_registry.__file__).resolve()), "tasks": [
                {"id": row.id, "title": row.title, "author": row.author,
                 "assignee": row.assignee, "belongs_to": row.belongs_to(owner)}
                for row in rows
            ]})

        async def control_add_task(request: web.Request) -> web.Response:
            body = await request.json()
            task = await task_registry.create_task(
                title=body["title"], status=body.get("status", "open"), author="owner-a",
            )
            return web.json_response({"id": task.id})

        async def control_out_of_order_notes(request: web.Request) -> web.Response:
            payloads = []
            for title in ("Older live note", "Newer live note"):
                item_id = emit_attention_item(state, source="user", kind="note",
                    item_kind=ItemKind.USER_NOTE.value, title=title, body="Native snapshot record.", store=inbox)
                payloads.append(_redact_item(inbox.items[item_id].to_dict()))
            state.broadcast_ws("inbox_new_item", payloads[1])
            state.broadcast_ws("inbox_new_item", payloads[0])
            state.broadcast_ws("inbox_new_item", payloads[1])
            return web.json_response({"ids": [payload["id"] for payload in payloads]})

        async def control_websocket_offline(request: web.Request) -> web.Response:
            body = await request.json()
            settings["ws_offline"] = bool(body["offline"])
            if settings["ws_offline"]:
                await state.close_all_ws()
            return web.json_response({"offline": settings["ws_offline"]})

        async def control_websocket_state(request: web.Request) -> web.Response:
            return web.json_response({"open": sum(not ws.closed for ws in state._ws_clients)})

        async def control_trigger_pages(request: web.Request) -> web.Response:
            started_at = time.time()
            journal = ExecutionJournal(home)
            for index in range(120):
                await journal.append(ExecutionRecord(
                    run_id="" if index == 19 else f"trigger-page-{index}",
                    job_id="schedule-1", started_at=started_at + (120 - index) / 1000,
                    finished_at=started_at + (120 - index) / 1000, status="success",
                    summary="Scheduled work ran",
                ))
            hooks = ScriptHookStore(home)
            hooks.create({
                "id": "hook-1", "name": "Native lifecycle hook", "last_run": started_at + 1,
                "last_status": "ok", "run_count": 1,
            })
            state._hook_store = hooks
            EventTriggerStore(home / "event_triggers.json").upsert(EventTrigger(
                id="event-1", pattern="MemoryUpdate", fire_count=1,
                last_fired_at=started_at + 2,
            ))
            return web.json_response({"ok": True})

        async def control_workflow_store(request: web.Request) -> web.Response:
            body = await request.json()
            database = home / "workflows" / "runs.db"
            saved = home / "workflows" / "runs.db.saved"
            if body["unavailable"]:
                database.rename(saved)
                database.mkdir()
            else:
                database.rmdir()
                saved.rename(database)
            return web.json_response({"ok": True})

        control = web.Application()
        control.router.add_post("/delay", control_delay)
        control.router.add_get("/delay-state", control_delay_state)
        control.router.add_post("/delay-release", control_delay_release)
        control.router.add_post("/empty-inbox", control_empty_inbox)
        control.router.add_post("/replace-approval", control_replace_approval)
        control.router.add_post("/replace-on-success", control_replace_on_success)
        control.router.add_get("/approval-state", control_approval_state)
        control.router.add_get("/failures", control_failures)
        control.router.add_get("/task-ownership", control_task_ownership)
        control.router.add_post("/task", control_add_task)
        control.router.add_post("/out-of-order-notes", control_out_of_order_notes)
        control.router.add_post("/websocket-offline", control_websocket_offline)
        control.router.add_get("/websocket-state", control_websocket_state)
        control.router.add_post("/trigger-pages", control_trigger_pages)
        control.router.add_post("/workflow-store", control_workflow_store)
        api_runner = web.AppRunner(app)
        control_runner = web.AppRunner(control)
        await api_runner.setup()
        await control_runner.setup()
        api_site = web.TCPSite(api_runner, "127.0.0.1", 0)
        control_site = web.TCPSite(control_runner, "127.0.0.1", 0)
        await api_site.start()
        await control_site.start()
        print(json.dumps({
            "api_port": api_site._server.sockets[0].getsockname()[1],
            "control_port": control_site._server.sockets[0].getsockname()[1],
        }), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            for task in approval_requests.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*approval_requests.values(), return_exceptions=True)
            await api_runner.cleanup()
            await control_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
