from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.gsd import GsdError, edit, inspect, projects, request_phase


async def gsd(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        identifier = request.match_info.get('id')
        if request.method == 'PUT':
            result = await edit(identifier, await read_json_body(request))
        elif request.method == 'POST':
            result = await request_phase(identifier, await read_json_body(request), 'user:' + str(request['user']))
        else:
            result = inspect(identifier, request.query.get('document')) if identifier else projects()
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except GsdError as error:
        return web.json_response({'error': str(error)}, status=error.status)
    except (ValueError, TypeError, OSError):
        return web.json_response({'error': 'Planning artifacts are invalid or unavailable'}, status=400)


def register(app):
    prefix = '/api/capabilities/platform/gsd'
    app.router.add_get(prefix, gsd)
    app.router.add_get(prefix + '/{id}', gsd)
    app.router.add_put(prefix + '/{id}', gsd)
    app.router.add_post(prefix + '/{id}/phase', gsd)
