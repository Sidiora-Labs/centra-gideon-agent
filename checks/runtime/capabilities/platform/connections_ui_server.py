import asyncio
import json
from aiohttp import web
from gideon.core.config.loader import config_path
from gideon.interfaces.dashboard.handlers.capabilities_connections import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"providers": [{"name": "primary", "type": "openai", "model": "alpha"}]}))
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
