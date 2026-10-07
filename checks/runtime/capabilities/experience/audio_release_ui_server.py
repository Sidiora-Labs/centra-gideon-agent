"""Authenticated native speech lease fixture with a controlled in-flight request."""

import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace.capabilities.experience import ExperienceStore


async def main():
    pending = asyncio.Event()
    finish = asyncio.Event()

    @web.middleware
    async def hold_inflight(request, handler):
        if request.path.endswith("/proactive-speech"):
            pending.set()
            await finish.wait()
        return await handler(request)

    app = web.Application(middlewares=[token_auth_middleware(), hold_inflight])
    app[STORE] = ExperienceStore(Path(os.environ["GIDEON_HOME"]))
    register(app)

    async def pending_state(request):
        return web.json_response({"pending": pending.is_set()})

    app.router.add_get("/api/test/pending", pending_state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "port": site._server.sockets[0].getsockname()[1],
                "token": generate_token("audio-test-owner"),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        finish.set()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
