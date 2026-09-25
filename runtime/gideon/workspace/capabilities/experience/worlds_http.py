import asyncio
import aiohttp
from aiohttp import web
from gideon.core.http_request import read_json_body
from .worlds import get_worlds, identifier
from .store import Conflict, NotFound


def register_worlds(app, store):
    worlds = get_worlds(store)
    async def handle(request):
        try:
            world = identifier(request.match_info['world'])
            action = request.path.rsplit('/', 1)[-1]
            if request.method == 'GET':
                result = await worlds.sources() if action == 'sources' else await worlds.call(world)
            else:
                body = await read_json_body(request)
                result = await worlds.open(world, body) if action == 'open' else await worlds.mutate(world, action, body)
            return web.json_response(result)
        except NotFound as exc:
            return web.json_response({'error':str(exc)}, status=404)
        except Conflict as exc:
            return web.json_response({'error':str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({'error':str(exc)}, status=400)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return web.json_response({'error':'World engine unavailable: ' + str(exc)}, status=502)
    async def cleanup(_app):
        await worlds.close()
    app.on_cleanup.append(cleanup)
    prefix = '/api/capabilities/experience/worlds/{world}'
    app.router.add_get(prefix, handle)
    app.router.add_get(prefix + '/sources', handle)
    for action in ('open','project','objects'):
        app.router.add_post(prefix + '/' + action, handle)
