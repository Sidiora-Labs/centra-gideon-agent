import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_assembly import store_at
from gideon.interfaces.dashboard.handlers.capabilities_music_assemblies import register
from gideon.interfaces.dashboard.handlers.capabilities_music_models3d import (
    register as models_register,
)
from gideon.workspace.capabilities.music.image3d import Image3DStore


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-assembly-ui-") as directory:
        home = Path(directory)
        store = store_at(home)
        app = web.Application()
        register(app, store)
        models_register(app, Image3DStore(home, store.artifacts))
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        print(json.dumps({"port": runner.addresses[0][1]}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
