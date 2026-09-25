"""An actual catalog HTTP service with a canonical authored WAV test fixture."""

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_catalog import artifact, catalog_at
from gideon.interfaces.dashboard.handlers.capabilities_music_catalog import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-catalog-ui-") as directory:
        store = catalog_at(Path(directory))
        audio = artifact(store)
        app = web.Application()
        register(app, store)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        print(
            json.dumps({"port": runner.addresses[0][1], "slug": audio.slug}), flush=True
        )
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
