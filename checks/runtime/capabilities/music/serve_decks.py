import asyncio
import json
import tempfile
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_music_decks import register
from test_decks import store_at,picture

async def main():
    with tempfile.TemporaryDirectory(prefix='gideon-decks-ui-') as directory:
        store=store_at(Path(directory));ref=picture(store)
        app=web.Application();register(app,store)
        runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
        print(json.dumps({'port':runner.addresses[0][1],'image':ref}),flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(main())
