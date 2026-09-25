"""Real local HTTP application for console integration tests."""

import asyncio
import sys
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.interfaces.dashboard.handlers.capabilities_experience_moltbook import register as register_moltbook
from gideon.workspace.capabilities.experience import ExperienceStore


async def main():
    @web.middleware
    async def owner(request, handler):
        request["user"] = "experience-owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app[STORE] = ExperienceStore(Path(sys.argv[1]))
    register(app)
    register_moltbook(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
