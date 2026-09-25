"""Review previews, materialization and canonical schedules for the bound runtime."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.integrations.action_providers.registry import register_action_provider
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.interfaces.dashboard.handlers.capabilities_knowledge import _redact
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.review_schedule import (
    ReviewActionProvider,
    ReviewSchedules,
)
from gideon.workspace.capabilities.knowledge.reviews import ReviewService


async def operation(request):
    try:
        preview = request.path.endswith("/preview")
        schedules = request.path.endswith("/schedules")
        allowed = (
            {"period", "date", "timezone"}
            if preview
            else (
                {"limit", "offset"}
                if request.method == "GET" and not schedules
                else set()
            )
        )
        if set(request.query) - allowed or any(
            len(request.query.getall(key)) != 1 for key in request.query
        ):
            raise CaptureError("Unknown or repeated review query")
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (
            request.method != "GET" and _is_restricted_session(state, request)
        ):
            raise CaptureError("This session cannot access personal reviews", 403)
        service = request.app["capability_review_service"]
        if preview:
            result = service.preview(
                request.query.get("period", "daily"),
                request.query.get("date"),
                request.query.get("timezone", "UTC"),
            )
            for section in result["sections"]:
                result["sections"][section] = [
                    {**row, "title": _redact(row)["title"]}
                    for row in result["sections"][section]
                ]
        elif schedules:
            selected = request.app["capability_review_schedules"]
            result = (
                selected.list()
                if request.method == "GET"
                else selected.save(await read_json_body(request))
            )
        elif request.method == "GET":
            result = service.list(
                int(request.query.get("limit", "20")),
                int(request.query.get("offset", "0")),
            )
        else:
            result = service.save(await read_json_body(request))
        return web.json_response(result)
    except (ValueError, TypeError) as exc:
        return web.json_response(
            {"error": str(exc)}, status=getattr(exc, "status", 400)
        )


def register(app):
    service = ReviewService(app["state"].knowledge_store)
    schedules = ReviewSchedules(service)
    app["capability_review_service"], app["capability_review_schedules"] = (
        service,
        schedules,
    )
    register_action_provider(ReviewActionProvider(schedules))
    root = "/api/capabilities/knowledge/reviews"
    app.router.add_get(root, operation)
    app.router.add_post(root, operation)
    app.router.add_get(root + "/preview", operation)
    app.router.add_get(root + "/schedules", operation)
    app.router.add_post(root + "/schedules", operation)
