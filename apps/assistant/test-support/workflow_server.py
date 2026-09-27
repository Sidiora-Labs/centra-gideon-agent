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
        app = web.Application(middlewares=[auth_middleware(
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
