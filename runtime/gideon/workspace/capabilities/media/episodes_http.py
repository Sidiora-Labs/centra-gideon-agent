import asyncio
from aiohttp import web
from .jobs_http import JOBS_KEY
from .sketches import SketchError


async def dispatch(request):
    try:
        if request.query:
            raise SketchError('Query parameters are not accepted')
        jobs = request.app[JOBS_KEY]
        episode_id = request.match_info.get('episode_id')
        if request.path.endswith('/scenes'):
            job = jobs.get(request.match_info['job_id'])
            if job['operation'] != 'episode_render':
                raise SketchError('Job is not an episode')
            value = jobs.episodes.scenes(job['id'])
        elif request.method == 'GET':
            value = jobs.episodes.history(episode_id) if request.path.endswith('/history') else jobs.episodes.get(episode_id) if episode_id else jobs.episodes.list()
        else:
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError('Invalid JSON') from exc
            value = await asyncio.to_thread(jobs.episodes.save, body, episode_id)
        return web.json_response(value, status=201 if request.method == 'POST' else 200)
    except SketchError as exc:
        return web.json_response({'error': str(exc)}, status=exc.status)


def register_episodes(app):
    base = '/api/capabilities/media/episodes'
    app.router.add_get(base, dispatch)
    app.router.add_post(base, dispatch)
    app.router.add_get(base+'/{episode_id}', dispatch)
    app.router.add_put(base+'/{episode_id}', dispatch)
    app.router.add_get(base+'/{episode_id}/history', dispatch)
    app.router.add_get('/api/capabilities/media/jobs/{job_id}/scenes', dispatch)
