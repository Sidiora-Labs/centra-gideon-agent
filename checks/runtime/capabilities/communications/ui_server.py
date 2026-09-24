import asyncio
import json
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_communications import register

async def main():
    app = web.Application()
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(json.dumps({'port': site._server.sockets[0].getsockname()[1]}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
