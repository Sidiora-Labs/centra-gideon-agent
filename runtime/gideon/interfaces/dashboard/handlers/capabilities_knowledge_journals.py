"""Canonical date journals and reviewed activity drafts for the bound runtime."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.interfaces.dashboard.handlers.capabilities_knowledge import _redact
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.journals import DateJournals


def public(value):
    if isinstance(value, dict):
        return {key: public(item) for key, item in _redact(value).items()}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


async def operation(request):
    try:
        allowed = {"date", "timezone"} if request.method == "GET" else set()
        if set(request.query) - allowed or any(
            len(request.query.getall(key)) != 1 for key in request.query
        ):
            raise CaptureError("Unknown or repeated journal query")
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (
            request.method != "GET" and _is_restricted_session(state, request)
        ):
            raise CaptureError("This session cannot access personal journals", 403)
        service = request.app["capability_date_journals"]
        if request.method == "POST":
            result = service.save(await read_json_body(request))
        else:
            arguments = {
                "date": request.query.get("date"),
                "timezone": request.query.get("timezone", "UTC"),
            }
            result = (
                service.draft(**arguments)
                if request.path.endswith("/draft")
                else service.get(**arguments)
            )
        return web.json_response(public(result))
    except (ValueError, TypeError) as exc:
        return web.json_response(
            {"error": str(exc)}, status=getattr(exc, "status", 400)
        )


def register(app):
    app["capability_date_journals"] = DateJournals(app["state"].knowledge_store)
    app.router.add_get("/api/capabilities/knowledge/journals", operation)
    app.router.add_post("/api/capabilities/knowledge/journals", operation)
    app.router.add_get("/api/capabilities/knowledge/journals/draft", operation)
