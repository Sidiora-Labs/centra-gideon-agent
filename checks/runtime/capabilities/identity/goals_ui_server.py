"""Real application used by the console interaction test."""

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_identity_goals import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app, store_path=Path(sys.argv[1]))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(
        json.dumps(
            {"port": port, "token": generate_token("identity-goals-test-owner")}
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
