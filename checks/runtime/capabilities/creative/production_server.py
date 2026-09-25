import asyncio
import signal
import sys

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register as register_creative
from gideon.interfaces.dashboard.handlers.capabilities_creative_production import register as register_production
from gideon.workspace.capabilities.creative.store import IngredientStore


async def main():
    home = sys.argv[1]
    app = web.Application()
    app[STORE] = IngredientStore(home)
    register_creative(app)
    register_production(app, home)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    await stopped.wait()
    await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
