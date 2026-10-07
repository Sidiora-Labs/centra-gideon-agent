import asyncio
import json
import signal
import sys

from aiohttp import web

from gideon.interfaces.dashboard.handlers import capabilities_creative
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE
from gideon.interfaces.dashboard.handlers.capabilities_creative_production import (
    register as register_production,
)
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace.capabilities.creative.store import IngredientStore


async def main():
    home = sys.argv[1]
    app = web.Application(middlewares=[token_auth_middleware()])
    app[STORE] = IngredientStore(home)
    capabilities_creative.register(app)
    register_production(app, home)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{runner.addresses[0][1]}",
                "token": generate_token("production-owner"),
            }
        ),
        flush=True,
    )
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    await stopped.wait()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
