import asyncio
from types import SimpleNamespace

from aiohttp import web

from gideon.automation.workflows import defs
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import config_dir
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.dashboard.handlers.capabilities_maintenance import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    workspace = config_dir() / "workspace"
    workspace.mkdir(parents=True)
    HierarchyStore().create_project("Maintenance project", workspace_dir=str(workspace))
    definitions = NativeWorkflowDefProvider()
    defs.register_provider(definitions)
    await definitions.save_def(
        name="code-project",
        root={"kind": "wait", "id": "actual", "config": {"duration_secs": 300}},
    )
    watchdog = WorkflowWatchdog()
    app = web.Application(middlewares=[token_auth_middleware()])
    app["state"] = SimpleNamespace(workflows=watchdog)
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await watchdog.stop()


asyncio.run(main())
