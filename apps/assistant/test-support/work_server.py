import asyncio
import json
import os
import secrets
import tempfile

from aiohttp import web


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-assistant-work-") as directory:
        os.environ["GIDEON_HOME"] = directory

        from gideon.engine.tasks import registry
        from gideon.engine.tasks.handlers import register_task_routes
        from gideon.engine.tasks.hierarchy import HierarchyStore
        from gideon.engine.tasks.native import NativeTaskProvider
        from gideon.interfaces.dashboard.token_auth import auth_middleware
        from gideon.security.auth.modes import AuthConfig, AuthMode

        registry._providers = {"native": NativeTaskProvider()}
        project = HierarchyStore().create_project("Source project")
        task = await registry.create_task(title="Source task", description="Durable native task")
        credential = secrets.token_urlsafe(24)
        os.environ["GIDEON_WORK_TEST_API_KEY"] = credential
        held_path = None
        held_method = "GET"
        started = asyncio.Event()
        release = asyncio.Event()
        settled = asyncio.Event()
        writes = {"task": 0, "project": 0}

        @web.middleware
        async def preflight_gate(request, handler):
            nonlocal held_path
            if request.method == held_method and request.path == held_path:
                held_path = None
                started.set()
                await release.wait()
                response = await handler(request)
                settled.set()
                return response
            if request.method == "PUT" and request.path == f"/api/tasks/{task.id}":
                writes["task"] += 1
            if request.method == "PUT" and request.path == f"/api/projects/{project.id}":
                writes["project"] += 1
            return await handler(request)

        async def control(request):
            nonlocal held_path, held_method
            action = request.match_info["action"]
            if action == "arm":
                held_path = request.query["path"]
                held_method = request.query.get("method", "GET")
                started.clear()
                release.clear()
                settled.clear()
            elif action == "wait":
                await asyncio.wait_for(started.wait(), timeout=10)
            elif action == "settled":
                await asyncio.wait_for(settled.wait(), timeout=10)
            elif action == "release":
                release.set()
            elif action == "revoke":
                os.environ["GIDEON_WORK_TEST_API_KEY"] = secrets.token_urlsafe(24)
            elif action != "state":
                return web.json_response({"error": "unknown action"}, status=404)
            return web.json_response({"writes": writes, "held": started.is_set() and not release.is_set()})

        app = web.Application(middlewares=[preflight_gate, auth_middleware(
            AuthConfig(mode=AuthMode.API_KEY, api_key_env="GIDEON_WORK_TEST_API_KEY"))])
        register_task_routes(app)
        controls = web.Application()
        controls.router.add_route("*", "/test/{action}", control)
        runner = web.AppRunner(app)
        control_runner = web.AppRunner(controls)
        await runner.setup()
        await control_runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        control_site = web.TCPSite(control_runner, "127.0.0.1", 0)
        await site.start()
        await control_site.start()
        port = site._server.sockets[0].getsockname()[1]
        control_port = control_site._server.sockets[0].getsockname()[1]
        print(json.dumps({"port": port, "control_port": control_port, "credential": credential,
                          "task_id": task.id, "project_id": project.id}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await control_runner.cleanup()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
