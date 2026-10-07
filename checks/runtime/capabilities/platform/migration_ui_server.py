import asyncio
import json
from types import SimpleNamespace

from aiohttp import web

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import config_dir
from gideon.interfaces.dashboard.handlers.capabilities_music import (
    register as register_music,
)
from gideon.interfaces.dashboard.handlers.capabilities_platform_migration import (
    register,
)
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    app["state"] = SimpleNamespace(
        knowledge_store=KnowledgeStore(str(config_dir() / "knowledge.db"))
    )
    register(app)
    register_music(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(
        json.dumps(
            {
                "port": runner.addresses[0][1],
                "token": generate_token("migration-test-owner"),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
