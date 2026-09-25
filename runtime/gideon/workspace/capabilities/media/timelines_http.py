import asyncio
from aiohttp import web
from .jobs_http import JOBS_KEY
from .sketches import SketchError


async def dispatch(request):
    try:
        if request.query:
            raise SketchError('Query parameters are not accepted')
        store = request.app[JOBS_KEY].timelines
        timeline_id = request.match_info.get('timeline_id')
        if request.method == 'GET':
            value = store.history(timeline_id) if request.path.endswith('/history') else store.get(timeline_id) if timeline_id else store.list()
        else:
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError('Invalid JSON') from exc
            value = await asyncio.to_thread(store.save, body, timeline_id)
        return web.json_response(value, status=201 if request.method == 'POST' else 200)
    except SketchError as exc:
        return web.json_response({'error': str(exc)}, status=exc.status)


def register_timelines(app):
    base = '/api/capabilities/media/timelines'
    app.router.add_get(base, dispatch)
    app.router.add_post(base, dispatch)
    app.router.add_get(base+'/{timeline_id}', dispatch)
    app.router.add_put(base+'/{timeline_id}', dispatch)
    app.router.add_get(base+'/{timeline_id}/history', dispatch)
