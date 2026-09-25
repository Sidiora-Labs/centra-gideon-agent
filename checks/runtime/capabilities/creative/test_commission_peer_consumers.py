import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers.capabilities_creative_commissions import COMMISSIONS, register
from gideon.interfaces.dashboard.handlers.capabilities_creative_peer_feedback import PEER_FEEDBACK, register as register_peer_feedback
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.workspace.capabilities.creative.commission_tools import CommissionTools
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.peer_feedback import PeerFeedbackStore, RECEIVE_PATH, SCOPE
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform.peers import PeerStore


def connect(store, remote, endpoint, send=(), receive=()):
    identity = remote.snapshot()['self']
    return store.put(identity['peer_id'], {'label': 'Review partner', 'endpoint': endpoint,
        'public_key': identity['public_key'], 'enabled': True, 'send_categories': list(send),
        'receive_categories': list(receive), 'revision': 0})


async def fixture(home):
    works = WorkStore(home)
    work = works.create({'request_id': 'consumer-work', 'title': 'Consumer source', 'kind': 'work',
        'prompt': 'A source.', 'author_ref': None, 'universe_ref': None, 'active_draft_id': None})
    work = works.draft(work['id'], {'request_id': 'consumer-draft', 'revision': 1,
        'text': 'A canonical source for a feedback consumer.', 'note': 'fixture'})['work']
    direction = DirectionStore(home)
    app = web.Application(); register(app, home, direction=direction, triggers=TriggerStore(base_dir=home))
    store = app[COMMISSIONS]
    commission_payload = {'request_id': 'consumer-commission', 'name': 'Consumer commission',
        'target_ability': 'image', 'brief': {'intent': 'Plan a still image.', 'genre': '', 'category': '',
        'style': '', 'constraints': {}, 'seed_refs': []}, 'cadence': {'kind': 'interval', 'seconds': 900},
        'sources': [{'kind': 'work', 'id': work['id'], 'revision': work['revision']}],
        'steps': [{'id': 'verify', 'title': 'Verify', 'operation': 'source.verify', 'depends_on': []},
                  {'id': 'snapshot', 'title': 'Snapshot', 'operation': 'treatment.snapshot', 'depends_on': ['verify']}],
        'enabled': True, 'max_attempts': 2}
    commission = store.create(commission_payload)
    run = await store.execute(commission['id'], 'manual:consumer', trigger='manual')
    output = {key: run['outputs'][0][key] for key in ('artifact_id', 'artifact_version', 'content_hash')}
    reaction = store.react(commission['id'], {'run_id': run['id'], 'author': 'dashboard-owner', 'output': output,
        'rating': 'liked', 'note': 'Keep this.', 'tags': ['review']})
    remote = home / 'remote'; remote.mkdir()
    remote_works = WorkStore(remote)
    remote_work = remote_works.create({'request_id': 'consumer-work', 'title': 'Consumer source', 'kind': 'work',
        'prompt': 'A source.', 'author_ref': None, 'universe_ref': None, 'active_draft_id': None})
    remote_work = remote_works.draft(remote_work['id'], {'request_id': 'consumer-draft', 'revision': 1,
        'text': 'A canonical source for a feedback consumer.', 'note': 'fixture'})['work']
    remote_direction = DirectionStore(remote)
    remote_store = CommissionStore(remote, direction=remote_direction, triggers=TriggerStore(base_dir=remote))
    remote_commission = remote_store.create({**commission_payload,
        'sources': [{'kind': 'work', 'id': remote_work['id'], 'revision': remote_work['revision']}]})
    remote_run = await remote_store.execute(remote_commission['id'], 'manual:consumer', trigger='manual')
    assert (remote_commission['id'], remote_run['id'], remote_run['project_id']) == (
        commission['id'], run['id'], run['project_id'])
    assert [{key: row[key] for key in ('artifact_id', 'artifact_version', 'content_hash')} for row in remote_run['outputs']] == [output]
    owner_peers, remote_peers = PeerStore(home), PeerStore(remote)
    receiver_app = web.Application(); calls = []
    @web.middleware
    async def count(request, handler):
        calls.append(request.path)
        return await handler(request)
    receiver_app.middlewares.append(count)
    register_peer_feedback(receiver_app, remote, commissions=remote_store, direction=remote_direction, peers=remote_peers)
    server = TestServer(receiver_app); await server.start_server()
    remote_row = connect(owner_peers, remote_peers, str(server.make_url('/')), send=[SCOPE])
    connect(remote_peers, owner_peers, 'http://127.0.0.1:9', receive=[SCOPE])
    sender = PeerFeedbackStore(home, commissions=store, direction=direction, peers=owner_peers)
    app[PEER_FEEDBACK] = sender
    return app, server, store, sender, commission, reaction, remote_row['id'], calls


async def test_owner_and_native_consumers_require_exact_revision_approval_and_durable_receipt(tmp_path):
    app, receiver, store, sender, commission, reaction, peer_id, calls = await fixture(tmp_path)
    root = '/api/capabilities/creative/commissions'
    approval = {'decision': 'approved', 'commission_id': commission['id'], 'peer_id': peer_id,
                'reaction_id': reaction['id'], 'reaction_revision': reaction['revision']}
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get(root + '/peer-feedback/peers')
            assert await response.json() == {'items': [{'id': peer_id, 'label': 'Review partner'}]}
            path = f"{root}/{commission['id']}/feedback/{reaction['id']}/deliver"
            response = await client.post(path, json={'peer_id': peer_id, 'endpoint': 'https://attacker.invalid',
                                                     'approval': approval})
            assert response.status == 400
            changed = {**approval, 'reaction_revision': reaction['revision'] + 1}
            response = await client.post(path, json={'peer_id': peer_id, 'approval': changed})
            assert response.status == 409
            assert calls == []
            response = await client.post(path, json={'peer_id': peer_id, 'approval': approval})
            first = await response.json()
            assert response.status == 200
            assert first['receipt']['state'] == 'applied'
            response = await client.post(path, json={'peer_id': peer_id, 'approval': approval})
            assert await response.json() == first
        assert calls == [RECEIVE_PATH]

        tools = CommissionTools(store, peer_feedback=sender)
        definitions = {row.name: row for row in await tools.list_tools()}
        assert definitions['creative_commission_peer_feedback_peers'].requires_approval is False
        assert definitions['creative_commission_peer_feedback_deliver'].requires_approval is True
        listed = await tools.invoke('creative_commission_peer_feedback_peers', {})
        assert json.loads(listed.output)['items'][0]['id'] == peer_id
        delivered = await tools.invoke('creative_commission_peer_feedback_deliver', {
            'id': commission['id'], 'reaction_id': reaction['id'], 'peer_id': peer_id, 'approval': approval})
        assert delivered.success is True
        assert json.loads(delivered.output) == first
        assert calls == [RECEIVE_PATH]
        with sender.db() as db:
            assert db.execute('SELECT COUNT(*) FROM creative_feedback_sends').fetchone()[0] == 1
    finally:
        await receiver.close()


async def test_owner_consumer_routes_remain_dashboard_token_protected(tmp_path):
    app = web.Application(middlewares=[token_auth_middleware(port=47992, local_only=True)])
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        peers = await client.get('/api/capabilities/creative/commissions/peer-feedback/peers')
        delivery = await client.post('/api/capabilities/creative/commissions/c/feedback/r/deliver', json={})
    assert peers.status == 403
    assert delivery.status == 403
