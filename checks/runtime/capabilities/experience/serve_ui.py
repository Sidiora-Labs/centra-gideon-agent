"""Real local HTTP application for console integration tests."""

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.interfaces.dashboard.handlers.capabilities_experience_moltbook import (
    register as register_moltbook,
)
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    app[STORE] = ExperienceStore(Path(sys.argv[1]))
    register(app)
    register_moltbook(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"port": site._server.sockets[0].getsockname()[1], "token": generate_token("experience-owner")}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
