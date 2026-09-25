import asyncio
import contextlib
import weakref
from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform import maintenance

_application = None


def supervisor():
    app = _application() if _application is not None else None
    return getattr(app.get('state'), 'workflows', None) if app is not None else None


async def endpoint(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        if request.method == 'GET':
            result = maintenance.view()
        else:
            body = await read_json_body(request)
            if 'id' in request.match_info:
                if not isinstance(body, dict) or set(body) != {'action'}:
                    raise ValueError('One maintenance action is required')
                result = await maintenance.control(request.match_info['id'], body['action'], supervisor())
            else:
                result = await maintenance.create(body, 'user:' + str(request['user']), supervisor())
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError) as error:
        return web.json_response({'error': str(error)}, status=409)


async def _lifecycle(app):
    async def drive():
        while True:
            for row in maintenance.view()['runs']:
                await maintenance.advance(row['id'], supervisor())
            await asyncio.sleep(1)
    task = asyncio.create_task(drive())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def register(app):
    global _application
    _application = weakref.ref(app)
    app.router.add_get('/api/capabilities/platform/maintenance', endpoint)
    app.router.add_post('/api/capabilities/platform/maintenance', endpoint)
    app.router.add_post('/api/capabilities/platform/maintenance/{id}', endpoint)
    app.cleanup_ctx.append(_lifecycle)
