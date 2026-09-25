"""Image-to-3D configuration, actual provider jobs and pinned model downloads."""
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.image3d import Image3DStore
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None):
    home=config_dir();store=store or Image3DStore(home,NativeArtifactProvider(root=home/'artifacts'))
    async def raw(request):
        try:
            version=int(request.match_info['version']);slug=request.match_info['slug']
            artifact=store.artifacts.get(slug,version=version)
            result=store.artifacts.raw_bytes(slug,version=version) if artifact else None
            if not result or artifact.kind!='model':
                raise DomainError('Model version unavailable',404,'not_found')
            return web.Response(body=result[0],content_type=result[1],headers={'X-Content-Type-Options':'nosniff'})
        except (DomainError,ValueError) as exc:
            return web.json_response({'error':str(exc)},status=getattr(exc,'status',400))
    async def handle(request):
        try:
            path=request.path;item_id=request.match_info.get('id')
            if path.endswith('/config'):
                return web.json_response({'config':store.config() if request.method=='GET' else store.configure(await read_json_body(request))})
            if path.endswith('/readiness'):
                return web.json_response(store.readiness())
            if item_id and request.method=='POST':
                if await read_json_body(request)!={}:
                    raise DomainError('Job operation takes no fields')
                return web.json_response({'job':await store.refresh(item_id) if path.endswith('/refresh') else store.stop(item_id)})
            if request.method=='POST':
                return web.json_response({'job':await store.submit(await read_json_body(request))},status=202)
            return web.json_response({'job':store.get(item_id)} if item_id else {'jobs':store.list()})
        except (DomainError,ValueError,TypeError) as exc:
            return web.json_response({'error':getattr(exc,'code','invalid_input'),'message':str(exc)},status=getattr(exc,'status',400))
    prefix='/api/capabilities/music/models3d'
    app.router.add_get(prefix+'/artifacts/{slug}/{version}/raw',raw)
    app.router.add_get(prefix+'/config',handle);app.router.add_patch(prefix+'/config',handle)
    app.router.add_get(prefix+'/readiness',handle);app.router.add_get(prefix+'/jobs',handle);app.router.add_post(prefix+'/jobs',handle)
    app.router.add_get(prefix+'/jobs/{id}',handle);app.router.add_post(prefix+'/jobs/{id}/refresh',handle);app.router.add_post(prefix+'/jobs/{id}/stop',handle)
