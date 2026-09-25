"""Musical canon authoring and part practice HTTP routes."""
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.rounds import RoundStore
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None):
    home = config_dir()
    store = store or RoundStore(home / 'capabilities' / 'music', MusicCatalog(home / 'capabilities' / 'music', NativeArtifactProvider(root=home / 'artifacts')))
    async def handle(request):
        try:
            item_id = request.match_info.get('id')
            if request.path.endswith('/practice'):
                return web.json_response({'items': store.history(item_id)} if request.method == 'GET' else store.practice(item_id, await read_json_body(request)))
            if request.method == 'GET':
                return web.json_response({'item': store.get(item_id)} if item_id else {'items': store.list(offset=int(request.query.get('offset', 0)), limit=int(request.query.get('limit', 50)))})
            data = await read_json_body(request)
            return web.json_response({'item': store.update(item_id, data) if item_id else store.create(data)}, status=200 if item_id else 201)
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response({'error': getattr(exc, 'code', 'invalid_input'), 'message': str(exc)}, status=getattr(exc, 'status', 400))
    prefix = '/api/capabilities/music/rounds'
    app.router.add_get(prefix, handle); app.router.add_post(prefix, handle)
    app.router.add_get(prefix + '/{id}', handle); app.router.add_patch(prefix + '/{id}', handle)
    app.router.add_get(prefix + '/{id}/practice', handle); app.router.add_post(prefix + '/{id}/practice', handle)
