from aiohttp import web
from gideon.core.http_request import read_json_body
from .avatar import AvatarStore
from .store import Conflict,NotFound

AVATARS=web.AppKey('experience_avatars',AvatarStore)


async def handle(request):
    store=request.app[AVATARS]
    try:
        if request.path.endswith('/raw'):
            data=store.raw(request.match_info['slug'],int(request.match_info['version']))
            return web.Response(body=data,content_type='model/gltf-binary',headers={'Cache-Control':'private, no-store'})
        if request.path.endswith('/avatar-selection'):
            result={'selection':store.selection() if request.method=='GET' else store.select(await read_json_body(request))}
        elif request.path.endswith('/avatar-models'):
            result={'models':store.models()}
        elif request.path.endswith('/bundled'):
            if await read_json_body(request)!={}:
                raise ValueError('Bundled avatar accepts no fields')
            result={'avatar':store.bundled()}
        elif request.method=='GET':
            result={'avatars':store.list()}
        else:
            result={'avatar':store.publish(await read_json_body(request))}
        return web.json_response(result,status=201 if request.method=='POST' else 200)
    except NotFound as exc:
        return web.json_response({'error':str(exc)},status=404)
    except Conflict as exc:
        return web.json_response({'error':str(exc)},status=409)
    except ValueError as exc:
        return web.json_response({'error':str(exc)},status=400)


def register_avatars(app,store):
    app[AVATARS]=AvatarStore(store)
    prefix='/api/capabilities/experience'
    app.router.add_get(prefix+'/avatars',handle)
    app.router.add_post(prefix+'/avatars',handle)
    app.router.add_post(prefix+'/avatars/bundled',handle)
    app.router.add_get(prefix+'/avatar-models',handle)
    app.router.add_get(prefix+'/avatar-selection',handle)
    app.router.add_put(prefix+'/avatar-selection',handle)
    app.router.add_get(prefix+'/avatar-assets/{slug}/{version}/raw',handle)
