import asyncio
import sqlite3
from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.peers import PeerError, PeerStore


def _authorized(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")


async def collection(request):
    _authorized(request)
    return web.json_response(await asyncio.to_thread(PeerStore().snapshot), headers={"Cache-Control": "no-store"})


async def item(request):
    _authorized(request)
    store = PeerStore()
    try:
        if request.method == "PUT":
            value = await asyncio.to_thread(store.put, request.match_info["peer_id"], await read_json_body(request))
            return web.json_response(value, headers={"Cache-Control": "no-store"})
        revision = int(request.query.get("revision", ""))
        await asyncio.to_thread(store.delete, request.match_info["peer_id"], revision)
        return web.json_response(await asyncio.to_thread(store.snapshot), headers={"Cache-Control": "no-store"})
    except PeerError as error:
        return web.json_response({"error": str(error)}, status=error.status)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid peer request"}, status=400)
    except sqlite3.Error:
        return web.json_response({"error": "Peer store unavailable"}, status=503)


async def verify(request):
    try:
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"proof", "payload"} or not isinstance(body["proof"], dict) or not isinstance(body["payload"], dict):
            raise PeerError("Signed peer envelope is invalid")
        peer = await asyncio.to_thread(PeerStore().verify_proof, body["proof"])
        return web.json_response({"verified": True, "peer": peer, "payload": body["payload"]}, headers={"Cache-Control": "no-store"})
    except PeerError as error:
        return web.json_response({"error": str(error)}, status=error.status)


async def probe(request):
    _authorized(request)
    try:
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"scope"}:
            raise PeerError("Probe scope is required")
        result = await PeerStore().post_signed(request.match_info["peer_id"], body["scope"], "/api/capabilities/platform/peers/proofs/verify", {"probe": True})
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except PeerError as error:
        return web.json_response({"error": str(error)}, status=error.status)


def register(app):
    app.router.add_get("/api/capabilities/platform/peers", collection)
    app.router.add_put("/api/capabilities/platform/peers/{peer_id}", item)
    app.router.add_delete("/api/capabilities/platform/peers/{peer_id}", item)
    app.router.add_post("/api/capabilities/platform/peers/proofs/verify", verify)
    app.router.add_post("/api/capabilities/platform/peers/{peer_id}/probe", probe)
