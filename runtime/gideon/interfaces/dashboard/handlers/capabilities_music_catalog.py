"""Music catalog routes using the parent application's authentication boundary."""
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None):
    store = store or MusicCatalog(config_dir() / 'capabilities' / 'music', NativeArtifactProvider())

    async def handle(request):
        try:
            kind, entity_id = request.match_info.get('kind', 'tracks'), request.match_info.get('id')
            if request.method == 'GET':
                if entity_id:
                    return web.json_response({'item': store.get(kind, entity_id)})
                archived = request.query.get('archived', 'false')
                if archived not in ('true', 'false'):
                    raise DomainError('Invalid archived filter')
                return web.json_response({'items': store.list(kind, q=request.query.get('q', ''), archived=archived == 'true', offset=int(request.query.get('offset', 0)), limit=int(request.query.get('limit', 50)))})
            data = await read_json_body(request)
            if request.path.endswith('/renders'):
                item = store.attach(entity_id, data)
            elif request.path.endswith('/select'):
                item = store.select(entity_id, data)
            elif request.method == 'PATCH':
                item = store.update(kind, entity_id, data)
            else:
                return web.json_response({'item': store.create(kind, data)}, status=201)
            return web.json_response({'item': item})
        except DomainError as exc:
            return web.json_response({'error': exc.code, 'message': str(exc)}, status=exc.status)
        except (ValueError, TypeError):
            return web.json_response({'error': 'invalid_input', 'message': 'Invalid catalog request'}, status=400)

    prefix = '/api/capabilities/music/catalog'
    app.router.add_get(prefix + '/{kind}', handle)
    app.router.add_post(prefix + '/{kind}', handle)
    app.router.add_get(prefix + '/{kind}/{id}', handle)
    app.router.add_patch(prefix + '/{kind}/{id}', handle)
    app.router.add_post(prefix + '/tracks/{id}/renders', handle)
    app.router.add_post(prefix + '/tracks/{id}/select', handle)
