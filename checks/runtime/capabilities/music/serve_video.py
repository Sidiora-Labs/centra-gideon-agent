import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_video import seeded
from gideon.interfaces.dashboard.handlers.capabilities_music_catalog import (
    register as catalog_register,
)
from gideon.interfaces.dashboard.handlers.capabilities_music_video import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-video-ui-") as directory:
        store, data = seeded(Path(directory))
        app = web.Application()
        register(app, store)
        catalog_register(app, store.catalog)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        print(
            json.dumps(
                {
                    "port": runner.addresses[0][1],
                    "selection": data["track_id"] + ":" + data["render_id"],
                    "slugs": [row["image_ref"]["slug"] for row in data["scenes"]],
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
