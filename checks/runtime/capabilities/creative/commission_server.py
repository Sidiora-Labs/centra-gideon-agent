import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers.capabilities_creative_commissions import COMMISSIONS, register
from gideon.interfaces.dashboard.handlers.capabilities_creative_peer_feedback import PEER_FEEDBACK, register as register_peer_feedback
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.peer_feedback import PeerFeedbackStore, SCOPE
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform.peers import PeerStore


async def main():
    home = Path(sys.argv[1]); works = WorkStore(home)
    work = works.create({'request_id': 'ui-work', 'title': 'UI source', 'kind': 'work', 'prompt': 'A source.',
                         'author_ref': None, 'universe_ref': None, 'active_draft_id': None})
    work = works.draft(work['id'], {'request_id': 'ui-draft', 'revision': 1,
        'text': 'A real canonical manuscript source.', 'note': 'UI fixture'})['work']
    app = web.Application(); direction = DirectionStore(home)
    register(app, home, direction=direction, triggers=TriggerStore(base_dir=home))
    remote = home / 'fixture-peer'; remote.mkdir()
    owner_peers, remote_peers = PeerStore(home), PeerStore(remote)
    remote_identity, owner_identity = remote_peers.snapshot()['self'], owner_peers.snapshot()['self']
    receiver_app = web.Application()
    register_peer_feedback(receiver_app, remote, commissions=app[COMMISSIONS], direction=direction, peers=remote_peers)
    receiver_runner = web.AppRunner(receiver_app); await receiver_runner.setup()
    receiver_site = web.TCPSite(receiver_runner, '127.0.0.1', 0); await receiver_site.start()
    receiver_origin = f"http://127.0.0.1:{receiver_site._server.sockets[0].getsockname()[1]}"
    owner_peers.put(remote_identity['peer_id'], {'label': 'Review partner', 'endpoint': receiver_origin,
        'public_key': remote_identity['public_key'], 'enabled': True, 'send_categories': [SCOPE],
        'receive_categories': [], 'revision': 0})
    remote_peers.put(owner_identity['peer_id'], {'label': 'Commission owner', 'endpoint': 'http://127.0.0.1:9',
        'public_key': owner_identity['public_key'], 'enabled': True, 'send_categories': [],
        'receive_categories': [SCOPE], 'revision': 0})
    app[PEER_FEEDBACK] = PeerFeedbackStore(home, commissions=app[COMMISSIONS], direction=direction, peers=owner_peers)
    async def fixture(request): return web.json_response({'work_id': work['id'], 'work_revision': work['revision'],
        'peer_id': remote_identity['peer_id']})
    async def deliveries(request):
        with app[PEER_FEEDBACK].db() as db:
            count = db.execute('SELECT COUNT(*) FROM creative_feedback_sends').fetchone()[0]
        return web.json_response({'count': count})
    app.router.add_get('/fixture', fixture)
    app.router.add_get('/fixture/deliveries', deliveries)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    while True: await asyncio.sleep(3600)


asyncio.run(main())
