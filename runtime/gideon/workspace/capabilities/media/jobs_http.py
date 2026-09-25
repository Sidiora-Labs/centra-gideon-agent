from aiohttp import web

from .jobs import MediaJobs
from .sketches import SketchError

JOBS_KEY = web.AppKey("media_jobs", MediaJobs)


async def jobs_dispatch(request):
    try:
        if request.query:
            raise SketchError("Query parameters are not accepted")
        jobs = request.app[JOBS_KEY]
        job_id = request.match_info.get("job_id")
        if request.method == "GET":
            result = jobs.get(job_id) if job_id else jobs.list()
        else:
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError("Invalid JSON") from exc
            if job_id:
                result = (
                    jobs.cancel(job_id, body)
                    if request.path.endswith("/cancel")
                    else jobs.retry(job_id, body)
                )
            else:
                result = jobs.submit(body)
        return web.json_response(
            result, status=202 if request.method == "POST" else 200
        )
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register_jobs(app, jobs):
    app[JOBS_KEY] = jobs
    base = "/api/capabilities/media/jobs"
    app.router.add_get(base, jobs_dispatch)
    app.router.add_post(base, jobs_dispatch)
    app.router.add_get(base + "/{job_id}", jobs_dispatch)
    app.router.add_post(base + "/{job_id}/cancel", jobs_dispatch)
    app.router.add_post(base + "/{job_id}/retry", jobs_dispatch)
