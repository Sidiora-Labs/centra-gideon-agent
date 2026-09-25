from aiohttp import web

from .annotations import AnnotationStore
from .sketches import SketchError

ANNOTATIONS_KEY = web.AppKey('media_annotations', AnnotationStore)


async def annotations_dispatch(request):
    try:
        history = request.method == 'GET' and request.path.endswith('/history')
        if request.query and (not history or set(request.query) - {'offset', 'limit'}):
            raise SketchError('Query parameters are not accepted')
        store = request.app[ANNOTATIONS_KEY]
        artifact_id = request.match_info['artifact_id']
        version = int(request.match_info['version'])
        if request.method == 'PUT':
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError('Invalid JSON') from exc
            result = store.save(artifact_id, version, body)
        elif 'revision' in request.match_info:
            result = store.get(artifact_id, version, int(request.match_info['revision']))
        elif history:
            result = store.history(artifact_id, version, int(request.query.get('offset', '0')), int(request.query.get('limit', '50')))
        else:
            result = store.get(artifact_id, version)
        return web.json_response(result)
    except (SketchError, ValueError) as exc:
        return web.json_response({'error': str(exc)}, status=getattr(exc, 'status', 400))


def register_annotations(app, store):
    app[ANNOTATIONS_KEY] = store
    base = '/api/capabilities/media/library/{artifact_id}/versions/{version}/annotations'
    app.router.add_get(base, annotations_dispatch)
    app.router.add_put(base, annotations_dispatch)
    app.router.add_get(base+'/history', annotations_dispatch)
    app.router.add_get(base+'/history/{revision}', annotations_dispatch)
