import asyncio
import json

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"port": site._server.sockets[0].getsockname()[1], "token": generate_token("communications-test-owner")}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
