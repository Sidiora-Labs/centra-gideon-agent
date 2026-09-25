"""Anniversary reads use only the runtime's bound stores."""

from aiohttp import web

from gideon.cognition.memory_service import MemoryService
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.workspace.capabilities.knowledge.anniversaries import anniversaries, source_record


def _stores(request):
    state = request.app["state"]
    if _blocks_reads_session(state, request):
        raise web.HTTPForbidden(text="This session cannot read personal knowledge.")
    builder = state.context_builder
    memory = builder.memory if builder is not None else getattr(state, "_standalone_memory", None)
    archive = getattr(memory, "vector_store", None)
    service = MemoryService.over_vector_store(archive) if archive is not None else None
    return state.knowledge_store, service


def _redact(row):
    result = dict(row)
    for key in ("title", "excerpt", "content"):
        if isinstance(result.get(key), str):
            clean, _ = redact_credentials(result[key])
            result[key], _ = redact_exfiltration_urls(clean)
    return result


async def get_anniversaries(request):
    allowed = {"date", "timezone", "limit", "offset"}
    if set(request.query) - allowed or any(len(request.query.getall(key)) != 1 for key in request.query):
        return web.json_response({"error": "Unknown or repeated anniversary query parameter"}, status=400)
    store, memory = _stores(request)
    try:
        result = anniversaries(
            store, memory, date=request.query.get("date"), timezone=request.query.get("timezone", "UTC"),
            limit=int(request.query.get("limit", "20")), offset=int(request.query.get("offset", "0")),
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    result["items"] = [_redact(row) for row in result["items"]]
    return web.json_response(result)


async def get_source(request):
    if request.query:
        return web.json_response({"error": "Source reads accept no query selectors"}, status=400)
    record = source_record(*_stores(request), request.match_info["source_type"], request.match_info["source_id"])
    if record is None:
        raise web.HTTPNotFound(text="The source is unavailable or was removed.")
    return web.json_response(_redact(record))


def register(app):
    app.router.add_get("/api/capabilities/knowledge/anniversaries", get_anniversaries)
    app.router.add_get("/api/capabilities/knowledge/sources/{source_type}/{source_id}", get_source)
