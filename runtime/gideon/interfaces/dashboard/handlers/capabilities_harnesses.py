from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.harnesses import HarnessError, change, inventory


async def harnesses(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        result = inventory() if request.method == 'GET' else await change(request.match_info['id'], await read_json_body(request))
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except HarnessError as error:
        return web.json_response({'error': str(error)}, status=error.status)
    except (ValueError, TypeError):
        return web.json_response({'error': 'Invalid harness request'}, status=400)


def register(app):
    app.router.add_get('/api/capabilities/platform/harnesses', harnesses)
    app.router.add_post('/api/capabilities/platform/harnesses/{id}', harnesses)
