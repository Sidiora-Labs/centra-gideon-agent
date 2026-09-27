import asyncio
import json
import os
import secrets
import tempfile

from aiohttp import web


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-assistant-workflows-") as directory:
        os.environ["GIDEON_HOME"] = directory

        from gideon.automation.workflows.handlers import register_workflow_routes
        from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
        from gideon.automation.workflows.defs import register_provider
        from gideon.interfaces.dashboard.token_auth import auth_middleware
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
        credential = secrets.token_urlsafe(24)
        os.environ["GIDEON_WORKFLOW_TEST_API_KEY"] = credential
        held_detail = False
        detail_started = asyncio.Event()
        release_detail = asyncio.Event()
        save_writes = 0

        async def controls(request: web.Request) -> web.Response:
            nonlocal held_detail, save_writes
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
                return web.json_response({"save_writes": save_writes})
            return web.json_response({"error": "unknown test control"}, status=404)

        @web.middleware
        async def test_control(request: web.Request, handler):
            nonlocal held_detail, save_writes
            if request.path.startswith("/__test/"):
                return await controls(request)
            if request.method == "POST" and request.path == "/api/workflows":
                body = await request.json()
                if body.get("save") is True:
                    save_writes += 1
            if request.method == "GET" and request.path.startswith("/api/workflows/") and request.path != "/api/workflows/runs" and held_detail:
                held_detail = False
                detail_started.set()
                await release_detail.wait()
            return await handler(request)

        app = web.Application(middlewares=[test_control, auth_middleware(
            AuthConfig(mode=AuthMode.API_KEY, api_key_env="GIDEON_WORKFLOW_TEST_API_KEY"))])
        register_workflow_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({"port": port, "credential": credential, "workflow_name": workflow_name}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
