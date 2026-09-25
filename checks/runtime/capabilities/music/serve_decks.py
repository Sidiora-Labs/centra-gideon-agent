import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_decks import picture, store_at
from gideon.interfaces.dashboard.handlers.capabilities_music_decks import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-decks-ui-") as directory:
        store = store_at(Path(directory))
        ref = picture(store)
        app = web.Application()
        register(app, store)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        print(json.dumps({"port": runner.addresses[0][1], "image": ref}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
