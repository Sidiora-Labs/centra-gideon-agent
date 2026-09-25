"""HTTP surface for recurring creative commissions and attributed feedback."""
from aiohttp import web

from gideon.workspace.capabilities.creative.commissions import CommissionStore
from gideon.workspace.capabilities.creative.store import CatalogError, identifier, keys


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


def error(exc):
    return web.json_response({'error': str(exc), 'code': 'creative_commission_invalid'}, status=getattr(exc, 'status', 400))


def register(app, home=None, direction=None, triggers=None, dispatcher=None):
    app[COMMISSIONS] = CommissionStore(home, direction=direction, triggers=triggers, dispatcher=dispatcher)
    root = '/api/capabilities/creative/commissions'
    app.router.add_get(root, collection); app.router.add_post(root, collection)
    app.router.add_get(root + '/{id}', item); app.router.add_patch(root + '/{id}', item)
    app.router.add_post(root + '/{id}/run', run)
    app.router.add_post(root + '/{id}/runs/{run_id}/retry', retry)
    app.router.add_get(root + '/{id}/feedback', feedback); app.router.add_post(root + '/{id}/feedback', feedback)
    app.router.add_delete(root + '/{id}/feedback/{reaction_id}', reaction)
