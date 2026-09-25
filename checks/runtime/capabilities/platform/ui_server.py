"""Actual dashboard handlers served on an isolated loopback port for UI journeys."""
import asyncio
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_platform import register
from gideon.interfaces.dashboard.handlers.prompts import api_prompt_syntax
from gideon.interfaces.dashboard.handlers.capabilities_harnesses import register as register_harnesses
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    register_harnesses(app)
    app.router.add_get("/api/prompts/syntax", api_prompt_syntax)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
