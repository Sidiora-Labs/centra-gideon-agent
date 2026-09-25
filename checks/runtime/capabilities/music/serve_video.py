import asyncio
import json
import tempfile
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_music_video import register
from gideon.interfaces.dashboard.handlers.capabilities_music_catalog import register as catalog_register
from test_video import seeded

async def main():
    with tempfile.TemporaryDirectory(prefix='gideon-video-ui-') as directory:
        store,data=seeded(Path(directory))
        app=web.Application();register(app,store);catalog_register(app,store.catalog)
        runner=web.AppRunner(app);await runner.setup()
        await web.TCPSite(runner,'127.0.0.1',0).start()
        print(json.dumps({'port':runner.addresses[0][1],'selection':data['track_id']+':'+data['render_id'],'slugs':[row['image_ref']['slug'] for row in data['scenes']]}),flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

if __name__=='__main__':
    asyncio.run(main())
