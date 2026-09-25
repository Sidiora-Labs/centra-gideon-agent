import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_catalog import artifact, attach_body
from checks.runtime.capabilities.music.test_rounds import store_at
from gideon.interfaces.dashboard.handlers.capabilities_music_catalog import (
    register as catalog_register,
)
from gideon.interfaces.dashboard.handlers.capabilities_music_rounds import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-rounds-ui-") as directory:
        store = store_at(Path(directory))
        track = store.catalog.create("tracks", {"title": "Actual voice recording"})
        audio = artifact(store.catalog)
        attached = store.catalog.attach(track["id"], attach_body(track, audio))
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
                    "slug": audio.slug,
                    "selection": track["id"] + ":" + attached["renders"][0]["id"],
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
