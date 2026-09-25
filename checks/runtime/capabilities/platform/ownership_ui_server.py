import asyncio
from aiohttp import web
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.dashboard.handlers.capabilities_ownership import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    HierarchyStore().create_project('Feature project')
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
