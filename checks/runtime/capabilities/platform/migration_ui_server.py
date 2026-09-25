import asyncio
from types import SimpleNamespace
from aiohttp import web
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import config_dir
from gideon.interfaces.dashboard.handlers.capabilities_music import register as register_music
from gideon.interfaces.dashboard.handlers.capabilities_platform_migration import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    app["state"] = SimpleNamespace(knowledge_store=KnowledgeStore(str(config_dir() / "knowledge.db")))
    register(app)
    register_music(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
