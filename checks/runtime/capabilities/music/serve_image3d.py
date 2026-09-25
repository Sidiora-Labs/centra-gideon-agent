import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_image3d import service_at, source
from gideon.interfaces.dashboard.handlers.capabilities_music_models3d import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-model-ui-") as directory:
        store = service_at(Path(directory))
        data = source(store)
        app = web.Application()
        register(app, store)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        print(
            json.dumps(
                {"port": runner.addresses[0][1], "slug": data["image_ref"]["slug"]}
            ),
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
