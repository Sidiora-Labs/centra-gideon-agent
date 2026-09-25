"""A real artifact-backed application for moodboard console qualification."""
import asyncio
from pathlib import Path
import sys
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative import IngredientStore
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.artifacts.registry import register_provider
from gideon.workspace.artifacts.handlers import api_artifact_raw


async def main():
    home = Path(sys.argv[1])
    provider = NativeArtifactProvider(home / "artifacts")
    register_provider(provider)
    provider.create(name="Literary sample", kind="markdown", content="The night train crossed the city.")
    provider.create(name="Long sample", kind="markdown", content="A" * 5000)
    catalog = IngredientStore(home)
    catalog.create({"request_id": "city", "type": "place", "title": "Linked city"})
    app = web.Application()
    app[STORE] = catalog
    register(app)
    app.router.add_get("/api/artifacts/{slug}/raw", api_artifact_raw)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
