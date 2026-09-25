from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers.agents import _get_config_lock
from gideon.workspace.capabilities.platform.comparisons import ComparisonError, add, remove, view


async def comparisons(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        if request.method != 'GET':
            async with _get_config_lock():
                if request.method == 'POST':
                    add(await read_json_body(request))
                else:
                    remove(request.match_info['id'])
        return web.json_response(view(request.query.get('run')), headers={'Cache-Control': 'no-store'})
    except ComparisonError as error:
        return web.json_response({'error': str(error)}, status=error.status)
    except (ValueError, TypeError):
        return web.json_response({'error': 'Invalid comparison request'}, status=400)


def register(app):
    app.router.add_get('/api/capabilities/platform/comparisons', comparisons)
    app.router.add_post('/api/capabilities/platform/comparisons', comparisons)
    app.router.add_delete('/api/capabilities/platform/comparisons/{id}', comparisons)
