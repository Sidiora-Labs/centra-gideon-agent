"""Real application used by the console interaction test."""
import asyncio
from pathlib import Path
import sys
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_identity import register


async def main():
    app = web.Application()
    register(app, store_path=Path(sys.argv[1]))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(f"http://127.0.0.1:{port}/api/capabilities/identity", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
