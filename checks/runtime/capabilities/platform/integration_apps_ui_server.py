import os
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_integration_apps import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.workspace.capabilities.platform.integration_apps.store import IntegrationApps


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    store = IntegrationApps(os.environ["GIDEON_HOME"], credential_resolver=lambda name: None)
    app["integration_apps_factory"] = lambda: store
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    try:
        await __import__("asyncio").Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    __import__("asyncio").run(main())
