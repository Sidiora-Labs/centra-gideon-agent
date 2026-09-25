import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_listening import (
    extended,
    payload,
    playlist_doc,
    store_at,
)
from gideon.interfaces.dashboard.handlers.capabilities_music_listening import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-listening-ui-") as directory:
        home = Path(directory)
        store = store_at(home)
        data = payload(store, [extended(), extended(master_metadata_track_name=None)])
        lists = payload(store, playlist_doc(), "spotify_playlists", "lists")
        app = web.Application()
        register(app, store)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        print(
            json.dumps(
                {
                    "port": runner.addresses[0][1],
                    "history": data["artifact_ref"],
                    "playlists": lists["artifact_ref"],
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
