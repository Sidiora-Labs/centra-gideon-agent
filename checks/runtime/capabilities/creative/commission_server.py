import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers.capabilities_creative_commissions import register
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.works import WorkStore


async def main():
    home = Path(sys.argv[1]); works = WorkStore(home)
    work = works.create({'request_id': 'ui-work', 'title': 'UI source', 'kind': 'work', 'prompt': 'A source.',
                         'author_ref': None, 'universe_ref': None, 'active_draft_id': None})
    work = works.draft(work['id'], {'request_id': 'ui-draft', 'revision': 1,
        'text': 'A real canonical manuscript source.', 'note': 'UI fixture'})['work']
    app = web.Application()
    register(app, home, direction=DirectionStore(home), triggers=TriggerStore(base_dir=home))
    async def fixture(request): return web.json_response({'work_id': work['id'], 'work_revision': work['revision']})
    app.router.add_get('/fixture', fixture)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    while True: await asyncio.sleep(3600)


asyncio.run(main())
