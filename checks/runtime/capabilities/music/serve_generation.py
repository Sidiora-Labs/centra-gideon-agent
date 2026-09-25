"""Real credential-free provider configuration server for console qualification."""

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_generation import service_at
from gideon.interfaces.dashboard.handlers.capabilities_music_generation import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-generation-ui-") as directory:
        app = web.Application()
        register(app, service_at(Path(directory)))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        print(runner.addresses[0][1], flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
