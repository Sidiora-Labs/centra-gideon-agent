import asyncio
import json

from aiohttp import web

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.dashboard.handlers.capabilities_ownership import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    HierarchyStore().create_project("Feature project")
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(json.dumps({"url": f"http://127.0.0.1:{runner.addresses[0][1]}", "token": generate_token("ownership-owner")}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
