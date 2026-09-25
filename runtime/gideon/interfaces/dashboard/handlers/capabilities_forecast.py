from aiohttp import web
from gideon.workspace.capabilities.platform.forecast import view


async def endpoint(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        return web.json_response(await view(horizon=int(request.query.get('horizon', 3600))), headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError):
        return web.json_response({'error': 'Schedule forecast unavailable or invalid horizon'}, status=400)


def register(app):
    app.router.add_get('/api/capabilities/platform/forecast', endpoint)
