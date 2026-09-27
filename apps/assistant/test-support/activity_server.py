import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web

import gideon.core.config.loader as loader
from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.automation.workflows import store as workflow_store
from gideon.automation.workflows.handlers import api_runs_list
from gideon.automation.workflows.models import RunStatus, WorkflowRun
from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.tasks import registry as task_registry
from gideon.engine.tasks.handlers import api_tasks_list
from gideon.integrations.inbox import InboxItem, InboxStore
from gideon.interfaces.dashboard import session_store, token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.messaging import api_notifications
from gideon.interfaces.dashboard.handlers.sessions import api_approvals
from gideon.interfaces.dashboard.handlers.triggers import api_trigger_history_all
from gideon.interfaces.dashboard.handlers_inbox import api_inbox_open_items
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.auth import credentials
from gideon.workspace.artifacts import registry as artifact_registry
from gideon.workspace.artifacts.handlers import api_artifacts_list


PASSWORD = "correct-horse-battery-staple"


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-activity-") as temporary_home:
        home = Path(temporary_home)
        loader.config_dir = lambda: home
        credentials.config_dir = lambda: home
        session_store.config_dir = lambda: home
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
            item_kind="agent_request", refs={"approval": "approval-1", "task_id": "task-related"},
            created_at=time.time(), owner="owner-a",
        ))
        inbox.save()
        state._inbox_store = inbox
        state._pending_approvals["approval-1"] = {
            "id": "approval-1", "source": "tool", "tool": "send", "session": "chat-1", "ts": time.time(),
        }
        state._notification_log = [{
            "kind": "system", "title": "Result ready", "body": "Open the result",
            "ts": str(time.time()), "acked": False,
        }]

        await task_registry.create_task(title="Another owner's task", status="open", author="owner-b")
        await asyncio.sleep(1.05)
        for index in range(22):
            await task_registry.create_task(title=f"Native task {index}", status="open", author="owner-a")
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

        settings = {"delay": 0.0, "delay_path": "/api/tasks", "workflow_failures": 0}

        @web.middleware
        async def delay_reads(request: web.Request, handler):
            if request.path == settings["delay_path"] and settings["delay"]:
                await asyncio.sleep(settings["delay"])
            try:
                response = await handler(request)
            except Exception:
                if request.path == "/api/workflows/runs":
                    settings["workflow_failures"] += 1
                raise
            if request.path == "/api/workflows/runs" and response.status >= 400:
                settings["workflow_failures"] += 1
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
        app.router.add_get("/api/workflows/runs", api_runs_list)
        app.router.add_get("/api/triggers/history", api_trigger_history_all)
        app.router.add_get("/api/inbox/open", api_inbox_open_items)
        app.router.add_get("/api/approvals", api_approvals)
        app.router.add_get("/api/notifications", api_notifications)
        app.router.add_get("/api/artifacts", api_artifacts_list)

        async def control_delay(request: web.Request) -> web.Response:
            body = await request.json()
            settings["delay"] = float(body["seconds"])
            settings["delay_path"] = body.get("path", "/api/tasks")
            return web.json_response({"ok": True})

        async def control_empty_inbox(request: web.Request) -> web.Response:
            inbox.items.clear()
            inbox.save()
            return web.json_response({"ok": True})

        async def control_failures(request: web.Request) -> web.Response:
            return web.json_response({"workflow": settings["workflow_failures"]})

        async def control_add_task(request: web.Request) -> web.Response:
            body = await request.json()
            task = await task_registry.create_task(
                title=body["title"], status=body.get("status", "open"), author="owner-a",
            )
            return web.json_response({"id": task.id})

        async def control_trigger_pages(request: web.Request) -> web.Response:
            started_at = time.time()
            journal = ExecutionJournal(home)
            for index in range(20):
                await journal.append(ExecutionRecord(
                    run_id="" if index == 19 else f"trigger-page-{index}",
                    job_id="schedule-1", started_at=started_at + index / 1000,
                    finished_at=started_at + index / 1000, status="success",
                    summary="Scheduled work ran",
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
        control.router.add_post("/empty-inbox", control_empty_inbox)
        control.router.add_get("/failures", control_failures)
        control.router.add_post("/task", control_add_task)
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
            await api_runner.cleanup()
            await control_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
