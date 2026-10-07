import asyncio
import json

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.dashboard.handlers.capabilities_gsd import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    workspace = config_dir() / "workspace"
    phase = workspace / ".planning/phases/01-editor"
    phase.mkdir(parents=True)
    (workspace / ".planning/STATE.md").write_text("Initial planning state.\n")
    (phase / "01-PLAN.md").write_text("Original phase plan.\n")
    HierarchyStore().create_project("Planning project", workspace_dir=str(workspace))
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(json.dumps({"port": runner.addresses[0][1], "token": generate_token("platform-owner")}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
