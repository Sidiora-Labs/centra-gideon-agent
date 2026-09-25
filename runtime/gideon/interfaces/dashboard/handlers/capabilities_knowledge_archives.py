"""Conversation archive operations over the application-bound knowledge store."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.workspace.capabilities.knowledge.archive import ConversationArchive
from gideon.workspace.capabilities.knowledge.capture import CaptureError


async def operation(request):
    try:
        allowed = (
            {"limit", "offset"}
            if request.method == "GET" and "id" not in request.match_info
            else set()
        )
        if set(request.query) - allowed or any(
            len(request.query.getall(key)) != 1 for key in request.query
        ):
            raise CaptureError("Unknown or repeated archive query")
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (
            request.path.endswith("/commit") and _is_restricted_session(state, request)
        ):
            raise CaptureError("This session cannot access conversation archives", 403)
        service = request.app["capability_conversation_archive"]
        if "id" in request.match_info:
            return web.Response(
                body=service.original(request.match_info["id"]),
                content_type="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="conversations.json"'
                },
            )
        if request.method == "GET":
            result = service.list(
                int(request.query.get("limit", "20")),
                int(request.query.get("offset", "0")),
            )
        else:
            body = await read_json_body(request)
            result = (
                service.commit(body)
                if request.path.endswith("/commit")
                else service.preview(body)
            )
        return web.json_response(result)
    except (ValueError, TypeError) as exc:
        return web.json_response(
            {"error": str(exc)}, status=getattr(exc, "status", 400)
        )


def register(app):
    app["capability_conversation_archive"] = ConversationArchive(
        app["state"].knowledge_store
    )
    root = "/api/capabilities/knowledge/archives"
    app.router.add_get(root, operation)
    app.router.add_get(root + "/sources/{id}", operation)
    app.router.add_post(root + "/preview", operation)
    app.router.add_post(root + "/commit", operation)
