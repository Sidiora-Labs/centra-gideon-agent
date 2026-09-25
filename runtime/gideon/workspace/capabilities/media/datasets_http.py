import asyncio

from aiohttp import web

from .jobs_http import JOBS_KEY
from .sketches import SketchError


async def dispatch(request):
    try:
        if request.query:
            raise SketchError("Query parameters are not accepted")
        jobs = request.app[JOBS_KEY]
        dataset_id = request.match_info.get("dataset_id")
        if request.path.endswith("/training"):
            result = jobs.trainer.readiness()
        elif request.path.endswith("/checkpoints"):
            job = jobs.get(request.match_info["job_id"])
            if job["operation"] != "lora_train":
                raise SketchError("Job is not a training run")
            result = jobs.trainer.checkpoints(job["id"])
        elif request.path.endswith("/export"):
            try:
                revision = int(request.match_info["revision"])
            except ValueError as exc:
                raise SketchError("Invalid dataset revision") from exc
            data = await asyncio.to_thread(jobs.datasets.export, dataset_id, revision)
            return web.Response(
                body=data,
                content_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="dataset.zip"'},
            )
        elif request.method == "GET":
            result = (
                jobs.datasets.history(dataset_id)
                if request.path.endswith("/history")
                else (
                    jobs.datasets.get(dataset_id)
                    if dataset_id
                    else jobs.datasets.list()
                )
            )
        else:
            try:
                body = await request.json()
            except (ValueError, TypeError) as exc:
                raise SketchError("Invalid JSON") from exc
            result = await asyncio.to_thread(jobs.datasets.save, body, dataset_id)
        return web.json_response(
            result, status=201 if request.method == "POST" else 200
        )
    except SketchError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


def register_datasets(app):
    base = "/api/capabilities/media/datasets"
    app.router.add_get(base, dispatch)
    app.router.add_post(base, dispatch)
    app.router.add_get(base + "/{dataset_id}", dispatch)
    app.router.add_put(base + "/{dataset_id}", dispatch)
    app.router.add_get(base + "/{dataset_id}/history", dispatch)
    app.router.add_get(base + "/{dataset_id}/revisions/{revision}/export", dispatch)
    app.router.add_get("/api/capabilities/media/training", dispatch)
    app.router.add_get("/api/capabilities/media/jobs/{job_id}/checkpoints", dispatch)
