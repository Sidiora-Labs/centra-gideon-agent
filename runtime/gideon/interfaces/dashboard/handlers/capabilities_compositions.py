from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard import views_store as store


async def endpoint(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        if request.method == 'GET':
            result = store.composition_state()
        elif request.method == 'POST':
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {'name'} or not isinstance(body['name'], str) or not 1 <= len(body['name'].strip()) <= 100:
                raise ValueError('A bounded view name is required')
            store.create_view(body['name'])
            result = store.composition_state()
        else:
            result = store.set_composition(request.match_info['id'], await read_json_body(request))
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError, store.PresetLockedError, store.ViewNotFoundError):
        return web.json_response({'error': 'Dashboard composition invalid, locked or changed; reload before saving'}, status=409)


def register(app):
    prefix = '/api/capabilities/platform/compositions'
    app.router.add_get(prefix, endpoint)
    app.router.add_post(prefix, endpoint)
    app.router.add_put(prefix + '/{id}', endpoint)
