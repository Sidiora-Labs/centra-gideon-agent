"""Signed peer ingress for receiver-owned creative commission feedback."""
import asyncio
import sqlite3

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.creative.peer_feedback import PeerFeedbackStore, RECEIVE_PATH
from gideon.workspace.capabilities.creative.store import CatalogError


PEER_FEEDBACK = web.AppKey("creative_peer_feedback", PeerFeedbackStore)


async def receive(request):
    try:
        envelope = await read_json_body(request)
        if (not isinstance(envelope, dict) or set(envelope) != {"proof", "payload"} or
                not isinstance(envelope.get("proof"), dict) or not isinstance(envelope.get("payload"), dict)):
            raise CatalogError("Signed peer-feedback envelope is invalid")
        result = await asyncio.to_thread(request.app[PEER_FEEDBACK].receive_signed, envelope)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except CatalogError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status, headers={"Cache-Control": "no-store"})
    except sqlite3.Error:
        return web.json_response({"error": "Peer-feedback store unavailable"}, status=503,
                                 headers={"Cache-Control": "no-store"})


def register(app, home=None, commissions=None, direction=None, peers=None):
    app[PEER_FEEDBACK] = PeerFeedbackStore(home, commissions=commissions, direction=direction, peers=peers)
    app.router.add_post(RECEIVE_PATH, receive)
