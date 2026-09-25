import asyncio
import json
import tempfile
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_music_rounds import register
from gideon.interfaces.dashboard.handlers.capabilities_music_catalog import register as catalog_register
from test_rounds import store_at
from test_catalog import artifact, attach_body

async def main():
    with tempfile.TemporaryDirectory(prefix='gideon-rounds-ui-') as directory:
        store = store_at(Path(directory))
        track = store.catalog.create('tracks', {'title': 'Actual voice recording'})
        audio = artifact(store.catalog)
        attached = store.catalog.attach(track['id'], attach_body(track, audio))
        app = web.Application()
        register(app, store)
        catalog_register(app, store.catalog)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        print(json.dumps({'port': runner.addresses[0][1], 'slug': audio.slug, 'selection': track['id'] + ':' + attached['renders'][0]['id']}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
