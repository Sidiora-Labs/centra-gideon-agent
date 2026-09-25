"""Real ephemeral HTTP application for console interaction acceptance."""
import asyncio
import tempfile
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_music import register
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music import RepertoireStore


async def main():
    with tempfile.TemporaryDirectory(prefix='gideon-music-ui-') as directory:
        home = Path(directory)
        artifacts = NativeArtifactProvider(root=home / 'artifacts')
        app = web.Application()
        register(app, RepertoireStore(home / 'music', artifacts))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        print(runner.addresses[0][1], flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
