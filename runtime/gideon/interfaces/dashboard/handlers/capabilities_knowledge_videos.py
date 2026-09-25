"""Reviewed transcript and guarded public-video ingestion endpoints."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.transcript_format import preview
from gideon.workspace.capabilities.knowledge.videos import VideoIngests


async def operation(request):
    try:
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (
            request.method != "GET" and _is_restricted_session(state, request)
        ):
            raise CaptureError("This session cannot access personal video ingests", 403)
        service = request.app["capability_video_ingests"]
        identity = request.match_info.get("identity")
        action = request.match_info.get("action", "")
        if request.path.endswith("/preview"):
            result = preview(await read_json_body(request))
        elif request.path.endswith("/import"):
            result = service.import_preview(await read_json_body(request))
        elif request.path.endswith("/fetch"):
            session_key = request.headers.get("X-Session-Key", "")
            if not session_key:
                raise CaptureError(
                    "An active session is required for video acquisition", 403
                )
            result = service.start_fetch(await read_json_body(request), session_key)
        elif action == "cancel":
            result = service.cancel(identity)
        elif action == "transcript":
            result = service.transcript(identity)
        elif identity:
            result = service.get(identity)
        else:
            allowed = {"limit", "offset"}
            if set(request.query) - allowed or any(
                len(request.query.getall(key)) != 1 for key in request.query
            ):
                raise CaptureError("Unknown or repeated video ingest query parameter")
            result = service.list(
                int(request.query.get("limit", "20")),
                int(request.query.get("offset", "0")),
            )
        return web.json_response(result)
    except (CaptureError, ValueError, TypeError) as exc:
        return web.json_response(
            {"error": str(exc)}, status=getattr(exc, "status", 400)
        )


def register(app):
    app["capability_video_ingests"] = VideoIngests(app["state"].knowledge_store)
    root = "/api/capabilities/knowledge/videos"
    app.router.add_get(root, operation)
    app.router.add_post(root + "/preview", operation)
    app.router.add_post(root + "/import", operation)
    app.router.add_post(root + "/fetch", operation)
    app.router.add_get(root + "/{identity}", operation)
    app.router.add_post(root + "/{identity}/{action:cancel}", operation)
    app.router.add_get(root + "/{identity}/{action:transcript}", operation)
