"""Explicit provider-backed music generation with durable local job receipts."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.generation import MusicGeneration
from gideon.workspace.capabilities.music.store import DomainError


def register(app, service=None):
    home = config_dir()
    service = service or MusicGeneration(
        home,
        MusicCatalog(
            home / "capabilities" / "music",
            NativeArtifactProvider(root=home / "artifacts"),
        ),
    )
    service.recover()

    async def handle(request):
        try:
            operation = request.match_info.get("operation")
            job_id = request.match_info.get("id")
            if job_id:
                result = (
                    await service.cancel(job_id)
                    if request.method == "POST"
                    else service.get(job_id)
                )
                return web.json_response({"job": result})
            if operation == "config":
                return web.json_response(
                    {
                        "config": (
                            service.configure(await read_json_body(request))
                            if request.method == "PATCH"
                            else service.config()
                        )
                    }
                )
            if operation == "readiness":
                return web.json_response(service.readiness())
            if request.method == "POST":
                return web.json_response(
                    {"job": await service.submit(await read_json_body(request))},
                    status=202,
                )
            return web.json_response(
                {
                    "jobs": service.list(
                        offset=int(request.query.get("offset", 0)),
                        limit=int(request.query.get("limit", 50)),
                    )
                }
            )
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response(
                {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)},
                status=getattr(exc, "status", 400),
            )

    prefix = "/api/capabilities/music/generation"
    app.router.add_get(prefix + "/{operation:config|readiness|jobs}", handle)
    app.router.add_patch(prefix + "/{operation:config}", handle)
    app.router.add_post(prefix + "/{operation:jobs}", handle)
    app.router.add_get(prefix + "/jobs/{id}", handle)
    app.router.add_post(prefix + "/jobs/{id}/cancel", handle)

    async def cleanup(application):
        await service.close()

    app.on_cleanup.append(cleanup)
