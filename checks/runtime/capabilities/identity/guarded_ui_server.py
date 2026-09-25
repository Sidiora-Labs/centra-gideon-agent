"""Actual idle native session and local tool dispatch behind real HTTP."""
import asyncio
from pathlib import Path
import sys
from aiohttp import web
from guarded_fixture import fixture
from gideon.interfaces.dashboard.handlers.capabilities_identity_guarded_recipes import register


async def main():
    home = Path(sys.argv[1])
    service, runtime, state, workspace, model = await fixture(home)
    app = web.Application()
    app['state'] = state
    register(app, home=home)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/identity/guarded-recipes', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await model.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
