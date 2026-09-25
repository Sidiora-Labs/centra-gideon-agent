"""RSVP state endpoints bound to canonical Knowledge items."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.rsvp import RsvpStates


async def operation(request):
    try:
        if request.query:
            raise CaptureError("RSVP operations do not accept query overrides")
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (
            request.method != "GET" and _is_restricted_session(state, request)
        ):
            raise CaptureError("This session cannot access RSVP state", 403)
        service = request.app["capability_knowledge_rsvp"]
        item_id = request.match_info["item_id"]
        action = request.match_info.get("action", "")
        if request.method == "GET":
            result = service.get(item_id)
        elif action == "bookmark":
            result = service.bookmark(item_id, await read_json_body(request))
        elif action == "restore":
            body = await read_json_body(request)
            if body not in ({}, None):
                raise CaptureError("Restore accepts no selectors")
            result = service.restore(item_id)
        elif not action:
            result = service.save(item_id, await read_json_body(request))
        else:
            raise CaptureError("Unknown RSVP operation", 404)
        return web.json_response(result)
    except (CaptureError, ValueError, TypeError, KeyError) as exc:
        return web.json_response(
            {"error": str(exc)}, status=getattr(exc, "status", 400)
        )


def register(app):
    app["capability_knowledge_rsvp"] = RsvpStates(app["state"].knowledge_store)
    root = "/api/capabilities/knowledge/rsvp/{item_id}"
    app.router.add_get(root, operation)
    app.router.add_put(root, operation)
    app.router.add_post(root + "/{action:bookmark|restore}", operation)
