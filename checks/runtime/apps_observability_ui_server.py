import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from gideon.extensions.apps import app_manager
from gideon.interfaces.dashboard.handlers.apps import register_app_routes
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    source = Path(os.environ["GIDEON_HOME"]) / "notes-source"
    source.mkdir(parents=True)
    manifest = {
        "name": "notes",
        "displayName": "Notes",
        "description": "Take notes",
        "version": "1.0.0",
    }
    (source / "app.json").write_text(json.dumps(manifest))
    result = app_manager.install(source, confirm=True, source_ref=str(source))
    if not result.ok:
        raise RuntimeError(result.error)
    manifest["version"] = "1.1.0"
    (source / "app.json").write_text(json.dumps(manifest))
    app = web.Application(middlewares=[token_auth_middleware()])
    register_app_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "port": site._server.sockets[0].getsockname()[1],
                "token": generate_token("app-observability-owner"),
                "source": str(source),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
