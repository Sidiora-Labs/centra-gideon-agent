import asyncio
import json
import tempfile
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_music_listening import register
from test_listening import store_at,payload,extended,playlist_doc

async def main():
    with tempfile.TemporaryDirectory(prefix='gideon-listening-ui-') as directory:
        home=Path(directory);store=store_at(home)
        data=payload(store,[extended(),extended(master_metadata_track_name=None)])
        lists=payload(store,playlist_doc(),'spotify_playlists','lists')
        app=web.Application();register(app,store)
        runner=web.AppRunner(app);await runner.setup()
        await web.TCPSite(runner,'127.0.0.1',0).start()
        print(json.dumps({'port':runner.addresses[0][1],'history':data['artifact_ref'],'playlists':lists['artifact_ref']}),flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(main())
