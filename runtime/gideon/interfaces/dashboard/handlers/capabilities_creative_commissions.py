"""HTTP surface for recurring creative commissions and attributed feedback."""
from aiohttp import web

from gideon.workspace.capabilities.creative.commissions import CommissionStore
from gideon.workspace.capabilities.creative.peer_feedback import SCOPE
from gideon.workspace.capabilities.creative.store import CatalogError, identifier, keys

from .capabilities_creative_peer_feedback import PEER_FEEDBACK


COMMISSIONS = web.AppKey('creative_commissions', CommissionStore)


async def collection(request):
    try:
        if request.query: raise CatalogError('Unexpected query parameter')
        store = request.app[COMMISSIONS]
        result = store.create(await request.json()) if request.method == 'POST' else store.list()
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return error(exc)


async def item(request):
    try:
        if request.query: raise CatalogError('Unexpected query parameter')
        store = request.app[COMMISSIONS]; identity = request.match_info['id']
        if request.method == 'PATCH': result = store.update(identity, await request.json())
        else: result = {**store.get(identity), 'runs': store.runs(identity)['items'], 'feedback': store.feedback(identity)['items']}
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return error(exc)


async def run(request):
    try:
        payload = await request.json(); keys(payload, {'request_id'})
        occurrence = 'manual:' + identifier(payload.get('request_id'))
        return web.json_response(await request.app[COMMISSIONS].execute(request.match_info['id'], occurrence, trigger='manual'))
    except (CatalogError, ValueError, TypeError) as exc:
        return error(exc)


async def retry(request):
    try:
        if (await request.json()) != {}: raise CatalogError('Retry accepts no fields')
        result = await request.app[COMMISSIONS].retry(request.match_info['id'], request.match_info['run_id'])
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return error(exc)


async def feedback(request):
    try:
        store = request.app[COMMISSIONS]; identity = request.match_info['id']
        result = store.react(identity, await request.json()) if request.method == 'POST' else store.feedback(identity)
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return error(exc)


async def reaction(request):
    try:
        result = request.app[COMMISSIONS].remove_reaction(
            request.match_info['id'], request.match_info['reaction_id'], await request.json())
        return web.json_response(result)
    except (CatalogError, ValueError, TypeError) as exc:
        return error(exc)


async def peer_feedback_peers(request):
    try:
        if request.query: raise CatalogError('Unexpected query parameter')
        peers = request.app[PEER_FEEDBACK].peers.snapshot()['peers']
        return web.json_response({'items': [
            {'id': row['id'], 'label': row['label']}
            for row in peers if row['enabled'] and SCOPE in row['send_categories']
        ]}, headers={'Cache-Control': 'no-store'})
    except (CatalogError, ValueError, TypeError, KeyError) as exc:
        return error(exc)


async def deliver_feedback(request):
    try:
        if request.query: raise CatalogError('Unexpected query parameter')
        body = await request.json(); keys(body, {'peer_id', 'approval'})
        peer_id = identifier(body.get('peer_id')); approval = body.get('approval')
        if not isinstance(approval, dict): raise CatalogError('Explicit peer-feedback approval is required', 403)
        keys(approval, {'decision', 'commission_id', 'peer_id', 'reaction_id', 'reaction_revision'})
        commission_id, reaction_id = request.match_info['id'], request.match_info['reaction_id']
        if (approval.get('decision') != 'approved' or approval.get('commission_id') != commission_id or
                approval.get('peer_id') != peer_id or approval.get('reaction_id') != reaction_id):
            raise CatalogError('Explicit peer-feedback approval does not match this delivery', 403)
        service = request.app[PEER_FEEDBACK]
        payload = service.export(peer_id, commission_id, reaction_id,
            {'decision': 'approved', 'peer_id': peer_id, 'reaction_id': reaction_id})
        if approval.get('reaction_revision') != payload['reaction']['revision']:
            raise CatalogError('Feedback changed after approval; reload', 409)
        receipt = await service.push_prepared(peer_id, payload)
        return web.json_response({'peer_id': peer_id, 'commission_id': commission_id,
            'reaction_id': reaction_id, 'reaction_revision': payload['reaction']['revision'],
            'receipt': receipt}, headers={'Cache-Control': 'no-store'})
    except (CatalogError, ValueError, TypeError, KeyError) as exc:
        return error(exc)


def error(exc):
    return web.json_response({'error': str(exc), 'code': 'creative_commission_invalid'}, status=getattr(exc, 'status', 400))


def register(app, home=None, direction=None, triggers=None, dispatcher=None):
    app[COMMISSIONS] = CommissionStore(home, direction=direction, triggers=triggers, dispatcher=dispatcher)
    root = '/api/capabilities/creative/commissions'
    app.router.add_get(root, collection); app.router.add_post(root, collection)
    app.router.add_get(root + '/peer-feedback/peers', peer_feedback_peers)
    app.router.add_get(root + '/{id}', item); app.router.add_patch(root + '/{id}', item)
    app.router.add_post(root + '/{id}/run', run)
    app.router.add_post(root + '/{id}/runs/{run_id}/retry', retry)
    app.router.add_get(root + '/{id}/feedback', feedback); app.router.add_post(root + '/{id}/feedback', feedback)
    app.router.add_delete(root + '/{id}/feedback/{reaction_id}', reaction)
    app.router.add_post(root + '/{id}/feedback/{reaction_id}/deliver', deliver_feedback)
