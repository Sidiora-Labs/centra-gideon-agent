"""A real artifact-backed application for moodboard console qualification."""

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.artifacts.handlers import api_artifact_raw
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.artifacts.registry import register_provider
from gideon.workspace.capabilities.creative import IngredientStore


async def main():
    home = Path(sys.argv[1])
    provider = NativeArtifactProvider(home / "artifacts")
    register_provider(provider)
    provider.create(
        name="Literary sample",
        kind="markdown",
        content="The night train crossed the city.",
    )
    provider.create(name="Long sample", kind="markdown", content="A" * 5000)
    catalog = IngredientStore(home)
    catalog.create({"request_id": "city", "type": "place", "title": "Linked city"})
    app = web.Application(middlewares=[token_auth_middleware()])
    app[STORE] = catalog
    register(app)
    app.router.add_get("/api/artifacts/{slug}/raw", api_artifact_raw)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"url": f"http://127.0.0.1:{runner.addresses[0][1]}", "token": generate_token("author_server-owner")}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
