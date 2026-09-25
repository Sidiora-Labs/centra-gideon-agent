"""Owner HTTP routes for remote agent session bridges."""

import asyncio

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.remote_sessions import (
    RemoteSessionBridge,
    RemoteSessionError,
)

PREFIX = "/api/capabilities/platform/remote-sessions"


def _owner(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Owner authentication required")


def _bridge(request):
    return request.app.get("remote_session_bridge") or RemoteSessionBridge(config_dir())


def _error(error):
    if isinstance(error, FileNotFoundError):
        return web.json_response({"error": "connection_not_found"}, status=404)
    if isinstance(error, RemoteSessionError):
        return web.json_response(
            {"error": error.code, "message": str(error)}, status=error.status
        )
    return web.json_response(
        {"error": "invalid_request", "message": str(error)}, status=400
    )


async def collection(request):
    _owner(request)
    bridge = _bridge(request)
    try:
        if request.method == "POST":
            saved = bridge.store.save_connection(await read_json_body(request))
            return web.json_response(saved, status=201)
        if request.query:
            raise ValueError("Unknown remote sessions query")
        rows = bridge.store.connections()
        for row in rows:
            row["retained_sessions"] = bridge.store.retained(row["id"])
        return web.json_response(
            {"connections": rows}, headers={"Cache-Control": "no-store"}
        )
    except (
        ValueError,
        TypeError,
        OSError,
        FileNotFoundError,
        RemoteSessionError,
    ) as error:
        return _error(error)


async def sessions(request):
    _owner(request)
    try:
        return web.json_response(
            await _bridge(request).sessions(request.match_info["connection_id"]),
            headers={"Cache-Control": "no-store"},
        )
    except (
        ValueError,
        TypeError,
        OSError,
        FileNotFoundError,
        RemoteSessionError,
    ) as error:
        return _error(error)


async def history(request):
    _owner(request)
    try:
        if set(request.query) - {"limit"}:
            raise ValueError("Unknown history query")
        return web.json_response(
            await _bridge(request).history(
                request.match_info["connection_id"],
                request.match_info["session_id"],
                request.query.get("limit", 50),
            ),
            headers={"Cache-Control": "no-store"},
        )
    except (
        ValueError,
        TypeError,
        OSError,
        FileNotFoundError,
        RemoteSessionError,
    ) as error:
        return _error(error)


async def stream(request):
    _owner(request)
    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"},
    )
    try:
        payload = await read_json_body(request)
        iterator = _bridge(request).stream(
            request.match_info["connection_id"],
            request.match_info["session_id"],
            payload,
        )
        await response.prepare(request)
        async for chunk in iterator:
            await response.write(chunk)
        await response.write_eof()
        return response
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except (
        ValueError,
        TypeError,
        OSError,
        FileNotFoundError,
        RemoteSessionError,
    ) as error:
        if response.prepared:
            await response.write(
                f"event: error\ndata: {{\"error\":\"{getattr(error, 'code', 'invalid_request')}\"}}\n\n".encode()
            )
            return response
        return _error(error)


def register(app):
    app.router.add_get(PREFIX, collection, allow_head=False)
    app.router.add_post(PREFIX, collection)
    app.router.add_get(PREFIX + "/{connection_id}/sessions", sessions, allow_head=False)
    app.router.add_get(
        PREFIX + "/{connection_id}/sessions/{session_id}/history",
        history,
        allow_head=False,
    )
    app.router.add_post(
        PREFIX + "/{connection_id}/sessions/{session_id}/messages/stream", stream
    )
