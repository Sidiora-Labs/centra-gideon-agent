from aiohttp import web

from .jobs_http import JOBS_KEY
from .sketches import SketchError


POLICY = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts"


async def preview(request):
    try:
        if request.query:
            raise SketchError('Query parameters are not accepted')
        jobs = request.app[JOBS_KEY]
        job = jobs.get(request.match_info['job_id'])
        if job['operation'] != 'code_animation_generate' or job['status'] != 'succeeded' or not job.get('result'):
            raise SketchError('Completed code animation is unavailable', 404)
        result = job['result']
        artifact = jobs.sketches.artifacts.get(result['artifact_id'], version=result['version'])
        if not artifact or artifact.kind != 'widget':
            raise SketchError('Code animation artifact is unavailable', 404)
        return web.Response(text=artifact.content, content_type='text/html', headers={'Content-Security-Policy': POLICY, 'X-Content-Type-Options': 'nosniff'})
    except SketchError as exc:
        return web.json_response({'error': str(exc)}, status=exc.status)


def register_animations(app):
    app.router.add_get('/api/capabilities/media/jobs/{job_id}/animation', preview)
