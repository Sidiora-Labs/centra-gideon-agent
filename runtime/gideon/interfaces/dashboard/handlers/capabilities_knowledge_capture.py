"""Capture HTTP operations bound to the application knowledge store at startup."""

import asyncio
from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session, _is_restricted_session
from gideon.workspace.capabilities.knowledge.capture import CaptureError, CaptureInbox


def _inbox(request, allowed=()):
    if set(request.query) - set(allowed) or any(len(request.query.getall(key)) != 1 for key in request.query):
        raise CaptureError("Unknown or repeated capture query parameter")
    state = request.app["state"]
    if _blocks_reads_session(state, request) or (request.method != "GET" and _is_restricted_session(state, request)):
        raise CaptureError("This session cannot access personal captures", 403)
    return request.app["capability_capture_inbox"]


def endpoint(function):
    async def wrapped(request):
        try:
            return web.json_response(await function(request))
        except CaptureError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return wrapped


@endpoint
async def captures(request):
    inbox = _inbox(request, ("limit", "offset") if request.method == "GET" else ())
    if request.method == "GET":
        return inbox.list(int(request.query.get("limit", "20")), int(request.query.get("offset", "0")))
    body = await read_json_body(request)
    if not isinstance(body, dict) or set(body) != {"request_id", "text"}:
        raise CaptureError("Text capture requires request_id and text only")
    return inbox.create(body["request_id"], body["text"])


@endpoint
async def detail(request):
    return _inbox(request).get(request.match_info["id"])


@endpoint
async def route(request):
    inbox = _inbox(request)
    body = await read_json_body(request)
    if not isinstance(body, dict):
        raise CaptureError("Route requires a JSON object")
    return inbox.route(request.match_info["id"], body)


@endpoint
async def audio(request):
    inbox = _inbox(request)
    if not request.content_type.startswith("multipart/"):
        raise CaptureError("Multipart audio upload required")
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "audio":
        raise CaptureError("First multipart field must be audio")
    data = bytearray()
    while chunk := await field.read_chunk():
        data.extend(chunk)
        if len(data) > 20 * 1024 * 1024:
            raise CaptureError("Audio exceeds 20 MiB", 413)
    if await reader.next() is not None:
        raise CaptureError("Only the audio field is accepted")
    return inbox.save_audio(request.headers.get("X-Capture-Request-ID"), bytes(data), field.filename or "recording.webm", field.headers.get("Content-Type", ""))


@endpoint
async def transcribe(request):
    inbox = _inbox(request)
    if request.can_read_body:
        raise CaptureError("Transcription accepts no body or model overrides")
    async with request.app["capability_capture_asr_lock"]:
        return await inbox.transcribe(request.match_info["id"])


def register(app):
    app["capability_capture_inbox"] = CaptureInbox(app["state"].knowledge_store)
    app["capability_capture_asr_lock"] = asyncio.Lock()
    root = "/api/capabilities/knowledge/captures"
    app.router.add_get(root, captures)
    app.router.add_post(root, captures)
    app.router.add_post(root + "/audio", audio)
    app.router.add_get(root + "/{id}", detail)
    app.router.add_post(root + "/{id}/route", route)
    app.router.add_post(root + "/{id}/transcribe", transcribe)
