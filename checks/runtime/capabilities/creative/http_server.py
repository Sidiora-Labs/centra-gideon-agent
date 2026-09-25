"""Real isolated application for console integration journeys."""

import asyncio
import sys

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative import IngredientStore


async def main():
    app = web.Application()
    app[STORE] = IngredientStore(sys.argv[1])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
