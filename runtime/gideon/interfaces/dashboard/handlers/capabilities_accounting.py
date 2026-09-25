from aiohttp import web
from gideon.workspace.capabilities.platform.accounting import view


async def endpoint(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        return web.json_response(view(days=int(request.query.get('days', 30))), headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError):
        return web.json_response({'error': 'Usage accounting unavailable or invalid window'}, status=400)


def register(app):
    app.router.add_get('/api/capabilities/platform/accounting', endpoint)
