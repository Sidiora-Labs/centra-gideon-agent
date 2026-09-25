import asyncio
import sqlite3

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.peers import PeerError
from gideon.workspace.capabilities.platform.remote_media import RemoteMedia, RemoteMediaError, SCOPE

REMOTE_MEDIA_KEY = web.AppKey("platform_remote_media", RemoteMedia)


def _authorized(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")


async def owner(request):
    _authorized(request)
    service = request.app[REMOTE_MEDIA_KEY]
    try:
        execution_id = request.match_info.get("execution_id")
        if request.method == "GET":
            result = await asyncio.to_thread(service.get, execution_id) if execution_id else await asyncio.to_thread(service.list)
        else:
            body = await read_json_body(request)
            if request.path.endswith("/refresh"):
                result = await service.refresh(execution_id)
            elif request.path.endswith("/cancel"):
                if not isinstance(body, dict) or set(body) != {"state_revision"}:
                    raise RemoteMediaError("Cancellation requires state_revision")
                result = await service.cancel(execution_id, body["state_revision"])
            else:
                if not isinstance(body, dict) or set(body) != {"peer_id", "request"} or not isinstance(body["peer_id"], str):
                    raise RemoteMediaError("Dispatch requires peer_id and request")
                result = await service.dispatch(body["peer_id"], body["request"])
        return web.json_response(result, status=202 if request.method == "POST" else 200, headers={"Cache-Control": "no-store"})
    except (RemoteMediaError, PeerError) as error:
        return web.json_response({"error": str(error)}, status=error.status)
    except sqlite3.Error:
        return web.json_response({"error": "Remote media store unavailable"}, status=503)


async def federation(request):
    service = request.app[REMOTE_MEDIA_KEY]
    try:
        if request.content_length and request.content_length > 32 * 1024:
            raise RemoteMediaError("Signed envelope exceeds 32 KiB", 413)
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"proof", "payload"} or not isinstance(body["proof"], dict) or not isinstance(body["payload"], dict):
            raise PeerError("Signed peer envelope is invalid")
        if body["proof"].get("scope") != SCOPE:
            raise PeerError("Remote media proof scope is invalid", 403)
        peer = await asyncio.to_thread(service.peers.verify_proof, body["proof"])
        action = request.match_info["action"]
        if action == "jobs":
            result = await asyncio.to_thread(service.accept, peer, body["payload"])
        elif action == "status":
            result = await asyncio.to_thread(service.status, peer, body["payload"])
        elif action == "cancel":
            result = await asyncio.to_thread(service.cancel_remote, peer, body["payload"])
        else:
            raise RemoteMediaError("Unknown remote media action", 404)
        return web.json_response(result, status=202 if action == "jobs" else 200, headers={"Cache-Control": "no-store"})
    except (RemoteMediaError, PeerError) as error:
        return web.json_response({"error": str(error)}, status=error.status)
    except sqlite3.Error:
        return web.json_response({"error": "Remote media store unavailable"}, status=503)


def register(app, service):
    app[REMOTE_MEDIA_KEY] = service
    base = "/api/capabilities/platform/remote-media"
    app.router.add_get(base, owner)
    app.router.add_post(base, owner)
    app.router.add_get(base + "/{execution_id}", owner)
    app.router.add_post(base + "/{execution_id}/refresh", owner)
    app.router.add_post(base + "/{execution_id}/cancel", owner)
    app.router.add_post(base + "/federation/{action}", federation)
