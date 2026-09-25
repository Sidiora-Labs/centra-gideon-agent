"""Serve the assembled capability handlers for real browser journeys."""
import asyncio
import json
import os
from pathlib import Path
from aiohttp import web
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.capabilities import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, reset_secret_cache


async def main():
    home=Path(os.environ['GIDEON_HOME'])
    home.mkdir(parents=True,exist_ok=True)
    reset_secret_cache()
    state=ConsoleState(ConversationDirectory(AppConfig()),start_time=0)
    app=web.Application(middlewares=[token_auth_middleware()])
    app['state']=state
    register(app)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    port=site._server.sockets[0].getsockname()[1]
    print(json.dumps({'port':port,'token':generate_token('browser-owner')}),flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        state.knowledge_store.close()


if __name__=='__main__':
    asyncio.run(main())
