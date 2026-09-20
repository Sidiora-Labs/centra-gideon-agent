"""Investigate Anywhere route (plan 60) — envelope composition + session staging.

POST /api/investigate {kind, id, back_link?}
  → 200 {session_key, context}
  → 400 unknown_kind | 404 unknown_entity (shared error envelopes)

Server-side effects: fresh dashboard chat session, task mode set to the
envelope's suggestion (default ``ask`` — read-only; the USER escalates, never the
button), envelope staged transiently on the session for the first-turn injection.
SEL: ``investigate_open``.
"""

from __future__ import annotations

import logging

from aiohttp import web

from gideon.cognition import investigate as inv
from gideon.core.http_request import read_json_body
from gideon.security.sel import sel

logger = logging.getLogger(__name__)


async def api_investigate(request: web.Request) -> web.Response:
    state = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response(
            {"error": {"code": "bad_request", "message": "invalid JSON body"}},
            status=400,
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": {"code": "bad_request", "message": "body must be an object"}},
            status=400,
        )
    kind = str(body.get("kind", ""))
    entity_id = str(body.get("id", ""))
    if not kind or not entity_id:
        return web.json_response(
            {"error": {"code": "bad_request", "message": "kind + id required"}},
            status=400,
        )
    try:
        ctx = await inv.resolve(kind, entity_id, state)
    except KeyError:
        return web.json_response(
            {
                "error": {
                    "code": "unknown_kind",
                    "message": f"no investigate resolver for kind {kind!r}; "
                    f"known: {list(inv.known_kinds())}",
                }
            },
            status=400,
        )
    except Exception:  # noqa: BLE001 — a resolver fault is an entity miss, not a 500
        logger.warning(
            "investigate resolver failed for %s:%s", kind, entity_id, exc_info=True
        )
        ctx = None
    if ctx is None:
        return web.json_response(
            {
                "error": {
                    "code": "unknown_entity",
                    "message": f"no {kind} entity {entity_id!r}",
                }
            },
            status=404,
        )
    if body.get("back_link"):
        ctx.back_link = str(body["back_link"])

    session = state.get_or_create_session()
    session._task_mode = ctx.suggested_task_mode or "ask"
    try:
        state.sessions.set_task_mode(f"dashboard:{session.key}", session._task_mode)
    except Exception:  # noqa: BLE001 — the session-level attr is the authoritative gate
        logger.debug("investigate task-mode push failed", exc_info=True)
    session._investigate_ctx = ctx.to_dict()

    try:
        sel().log_api_access(
            caller="dashboard:investigate",
            operation="investigate_open",
            outcome="success",
            resources=f"{kind}={entity_id} session={session.key}",
        )
    except Exception:  # noqa: BLE001
        logger.debug("investigate SEL log failed", exc_info=True)

    return web.json_response({"session_key": session.key, "context": ctx.to_dict()})


def register_investigate_routes(app: web.Application) -> None:
    app.router.add_post("/api/investigate", api_investigate)
