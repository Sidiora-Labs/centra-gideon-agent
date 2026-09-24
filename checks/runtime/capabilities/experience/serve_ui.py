"""Real local HTTP application for console integration tests."""

import asyncio
import sys
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore


async def main():
    app = web.Application()
    app[STORE] = ExperienceStore(Path(sys.argv[1]))
    register(app)
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
