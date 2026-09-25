import asyncio
import sqlite3
from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.ownership import OwnershipError, mutate, view


async def ownership(request):
    if not request.get('user') or request.get('app'):
        raise web.HTTPForbidden(text='Dashboard authentication required')
    try:
        if request.method == 'POST':
            body = await read_json_body(request)
            await asyncio.to_thread(mutate, body, 'user:' + str(request['user']))
        result = await asyncio.to_thread(view, request.query.get('project_id'), request.query.get('feature'))
        result['actor'] = 'user:' + str(request['user'])
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except OwnershipError as error:
        return web.json_response({'error': str(error)}, status=error.status)
    except sqlite3.Error:
        return web.json_response({'error': 'Ownership store unavailable'}, status=503)
    except (ValueError, TypeError):
        return web.json_response({'error': 'Invalid ownership request'}, status=400)


def register(app):
    app.router.add_get('/api/capabilities/platform/ownership', ownership)
    app.router.add_post('/api/capabilities/platform/ownership', ownership)
