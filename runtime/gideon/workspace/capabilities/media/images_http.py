from aiohttp import web

from .jobs_http import JOBS_KEY
from .sketches import SketchError


async def dispatch(request):
    try:
        if request.query:
            raise SketchError('Query parameters are not accepted')
        jobs = request.app[JOBS_KEY]
        if request.method == 'GET':
            if request.path.endswith('/videos'):
                return web.json_response(await jobs.videos.capabilities())
            result = await jobs.images.lora_inventory(request.match_info.get('adapter_id')) if '/loras' in request.path else await jobs.images.capabilities()
        else:
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError('Invalid JSON') from exc
            result = jobs.submit(body)
        return web.json_response(result, status=202 if request.method == 'POST' else 200)
    except SketchError as exc:
        return web.json_response({'error': str(exc)}, status=exc.status)
    except Exception:
        return web.json_response({'error': 'Image provider capability lookup failed'}, status=503)


def register_images(app):
    app.router.add_get('/api/capabilities/media/videos', dispatch)
    app.router.add_post('/api/capabilities/media/videos', dispatch)
    app.router.add_get('/api/capabilities/media/images', dispatch)
    app.router.add_post('/api/capabilities/media/images', dispatch)
    app.router.add_get('/api/capabilities/media/loras', dispatch)
    app.router.add_get('/api/capabilities/media/loras/{adapter_id}', dispatch)
