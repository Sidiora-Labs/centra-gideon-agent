"""Ordered link buckets and guarded repository study endpoints."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.links import LinkStudies


async def operation(request):
    try:
        if request.query:
            raise CaptureError("Link operations do not accept query overrides")
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (
            request.method != "GET" and _is_restricted_session(state, request)
        ):
            raise CaptureError(
                "This session cannot access personal link collections", 403
            )
        service = request.app["capability_link_studies"]
        identity = request.match_info.get("identity")
        action = request.match_info.get("action", "")
        if request.path.endswith("/buckets") and request.method == "GET":
            result = {"buckets": service.buckets()}
        elif request.path.endswith("/buckets"):
            result = service.create_bucket(await read_json_body(request))
        elif request.path.endswith("/buckets/reorder"):
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {"ids"}:
                raise CaptureError("Bucket reorder requires ids")
            result = {"buckets": service.reorder_buckets(body["ids"])}
        elif action == "bucket" and request.method == "PATCH":
            result = service.update_bucket(identity, await read_json_body(request))
        elif action == "bucket" and request.method == "DELETE":
            result = service.delete_bucket(identity)
        elif request.path.endswith("/links"):
            result = service.add_link(await read_json_body(request))
        elif action == "links-reorder":
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {"ids"}:
                raise CaptureError("Link reorder requires ids")
            result = {"links": service.reorder_links(identity, body["ids"])}
        elif request.path.endswith("/repositories") and request.method == "GET":
            result = service.repos()
        elif request.path.endswith("/repositories"):
            session_key = request.headers.get("X-Session-Key", "")
            if not session_key:
                raise CaptureError(
                    "An active session is required for repository intake", 403
                )
            result = await service.intake(await read_json_body(request), session_key)
        elif action == "repository":
            result = service.repo(identity)
        elif action == "study":
            result = service.restudy(identity)
        elif action == "report":
            result = service.report(identity)
        else:
            raise CaptureError("Unknown link operation", 404)
        return web.json_response(result)
    except (CaptureError, ValueError, TypeError) as exc:
        return web.json_response(
            {"error": str(exc)}, status=getattr(exc, "status", 400)
        )


def register(app):
    app["capability_link_studies"] = LinkStudies(app["state"].knowledge_store)
    root = "/api/capabilities/knowledge/links"
    app.router.add_get(root + "/buckets", operation)
    app.router.add_post(root + "/buckets", operation)
    app.router.add_post(root + "/buckets/reorder", operation)
    app.router.add_patch(root + "/buckets/{identity}/{action:bucket}", operation)
    app.router.add_delete(root + "/buckets/{identity}/{action:bucket}", operation)
    app.router.add_post(root + "/links", operation)
    app.router.add_post(root + "/buckets/{identity}/{action:links-reorder}", operation)
    app.router.add_get(root + "/repositories", operation)
    app.router.add_post(root + "/repositories", operation)
    app.router.add_get(root + "/repositories/{identity}/{action:repository}", operation)
    app.router.add_post(root + "/repositories/{identity}/{action:study}", operation)
    app.router.add_get(root + "/repositories/{identity}/{action:report}", operation)
