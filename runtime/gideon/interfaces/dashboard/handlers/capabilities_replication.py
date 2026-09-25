import asyncio
import sqlite3

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.platform.peers import PeerError, PeerStore
from gideon.workspace.capabilities.platform.replication import (
    ReplicationError,
    ReplicationService,
)


def _authorized(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")


async def status(request):
    _authorized(request)
    return web.json_response(
        await asyncio.to_thread(ReplicationService().status),
        headers={"Cache-Control": "no-store"},
    )


async def push(request):
    _authorized(request)
    try:
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"domain"}:
            raise ReplicationError("Replication domain is required")
        return web.json_response(
            await ReplicationService().push(
                request.match_info["peer_id"], body["domain"]
            ),
            headers={"Cache-Control": "no-store"},
        )
    except (ReplicationError, PeerError) as error:
        return web.json_response({"error": str(error)}, status=error.status)


async def receive(request):
    try:
        envelope = await read_json_body(request)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"proof", "payload"}
            or not isinstance(envelope["proof"], dict)
            or not isinstance(envelope["payload"], dict)
        ):
            raise ReplicationError("Signed replication envelope is invalid")
        peer = await asyncio.to_thread(PeerStore().verify_proof, envelope["proof"])
        result = await asyncio.to_thread(
            ReplicationService().apply_batch, peer["id"], envelope["payload"]
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except (ReplicationError, PeerError) as error:
        return web.json_response({"error": str(error)}, status=error.status)
    except sqlite3.Error:
        return web.json_response({"error": "Replication store unavailable"}, status=503)


async def restore_fields(request):
    _authorized(request)
    try:
        body = await read_json_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"fields"}
            or not isinstance(body["fields"], list)
        ):
            raise ReplicationError("Conflict fields are required")
        result = await asyncio.to_thread(
            ReplicationService().restore_fields,
            request.match_info["conflict_id"],
            body["fields"],
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except ReplicationError as error:
        return web.json_response({"error": str(error)}, status=error.status)


def register(app):
    app.router.add_get("/api/capabilities/platform/replication", status)
    app.router.add_post(
        "/api/capabilities/platform/replication/peers/{peer_id}/push", push
    )
    app.router.add_post("/api/capabilities/platform/replication/receive", receive)
    app.router.add_post(
        "/api/capabilities/platform/replication/conflicts/{conflict_id}/restore-fields",
        restore_fields,
    )
