import asyncio
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.registry import get_provider
from .media_shares import MediaShareError, MediaShares
from .peers import PeerError, PeerStore


def _authorized(request):
    if not request.get("user") or request.get("app"): raise web.HTTPForbidden(text="Dashboard authentication required")


def register_media_shares(app, service=None, peers=None):
    peers = peers or PeerStore()
    service = service or MediaShares(config_dir(), peers, get_provider())
    base = "/api/capabilities/platform/media-shares"

    async def collection(request):
        try:
            _authorized(request)
            if request.method == "GET": return web.json_response(await asyncio.to_thread(service.list), headers={"Cache-Control": "no-store"})
            return web.json_response(await service.share(await read_json_body(request)), status=202, headers={"Cache-Control": "no-store"})
        except MediaShareError as exc: return web.json_response({"error": str(exc)}, status=exc.status)

    async def revoke(request):
        try:
            _authorized(request); body = await read_json_body(request)
            if set(body) != {"revision"}: raise MediaShareError("Revocation requires revision only")
            return web.json_response(await service.revoke(request.match_info["id"], body["revision"]), headers={"Cache-Control": "no-store"})
        except MediaShareError as exc: return web.json_response({"error": str(exc)}, status=exc.status)

    async def receive(request):
        try:
            body = await read_json_body(request)
            if set(body) != {"proof", "payload"} or not isinstance(body["proof"], dict) or not isinstance(body["payload"], dict): raise MediaShareError("Signed media envelope is invalid")
            peer = await asyncio.to_thread(peers.verify_proof, body["proof"])
            return web.json_response(await asyncio.to_thread(service.receive, peer["id"], body["payload"]), headers={"Cache-Control": "no-store"})
        except (MediaShareError, PeerError) as exc: return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))

    app.router.add_get(base, collection)
    app.router.add_post(base, collection)
    app.router.add_post(base + "/{id}/revoke", revoke)
    app.router.add_post(base + "/receive", receive)
