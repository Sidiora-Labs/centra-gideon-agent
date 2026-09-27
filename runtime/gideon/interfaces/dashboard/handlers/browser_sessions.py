"""Authenticated customer browser reservation and metadata routes."""

from __future__ import annotations

import asyncio
import sqlite3

from aiohttp import web

from gideon.core.config import config_dir
from gideon.core.http_request import read_json_body
from gideon.integrations.browse.customer_sessions import (
    CustomerBrowserSessionStore,
    InvalidSessionTransition,
    SessionNotFound,
    StaleSessionVersion,
)
from gideon.interfaces.dashboard.chat_utils import candidate_history_keys

KEY = web.AppKey("customer_browser_sessions", CustomerBrowserSessionStore)
_LOCAL_ACCOUNT = "local"


def _owner(request: web.Request) -> str:
    owner = request.get("user")
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or request.get("app")
        or owner in {"dev-local", "api_key"}
        or owner.startswith("local-net:")
    ):
        raise web.HTTPUnauthorized(text="Authenticated customer required")
    return owner


def _canonical_conversation(request: web.Request, conversation_id: str) -> bool:
    state = request.app["state"]
    session = state.get_session(conversation_id)
    if session is not None:
        return (
            getattr(session, "memory_mode", "persistent") == "persistent"
            and not getattr(session, "_app", "")
        )
    log = state.conversation_log
    if log is None:
        return False
    for key in candidate_history_keys(conversation_id):
        if log.has_log(key):
            meta = log.get_metadata(key)
            return (
                bool(meta)
                and not meta.get("app")
                and meta.get("memory_mode", "persistent") == "persistent"
                and not meta.get("closed")
            )
    return False


async def _body(request: web.Request) -> dict:
    try:
        body = await read_json_body(request)
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text="Invalid JSON body") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="JSON body must be an object")
    return body


def _reply(session, status: int = 200) -> web.Response:
    return web.json_response(
        {"session": session.public()}, status=status, headers={"Cache-Control": "no-store"}
    )


async def create(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    conversation_id = body.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 255:
        raise web.HTTPBadRequest(text="conversation_id is required")
    store = request.app[KEY]
    try:
        existing = await asyncio.to_thread(
            store.find_conversation, _LOCAL_ACCOUNT, owner, conversation_id
        )
        if existing is not None:
            return _reply(existing)
        if not _canonical_conversation(request, conversation_id):
            raise SessionNotFound
        session, created = await asyncio.to_thread(
            store.reserve, _LOCAL_ACCOUNT, owner, conversation_id
        )
        return _reply(session, status=201 if created else 200)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except (sqlite3.Error, OSError):
        raise web.HTTPServiceUnavailable(text="Browser session store unavailable") from None


async def get(request: web.Request) -> web.Response:
    owner = _owner(request)
    try:
        session = await asyncio.to_thread(
            request.app[KEY].get,
            request.match_info["session_id"], _LOCAL_ACCOUNT, owner,
        )
        return _reply(session)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except sqlite3.Error:
        raise web.HTTPServiceUnavailable(text="Browser session store unavailable") from None


async def mutate(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    try:
        session = await asyncio.to_thread(
            request.app[KEY].transition,
            request.match_info["session_id"], _LOCAL_ACCOUNT, owner,
            expected_version=body.get("expected_version"),
            action=request.match_info["action"],
        )
        return _reply(session)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except StaleSessionVersion:
        raise web.HTTPConflict(text="Stale browser session version") from None
    except InvalidSessionTransition:
        raise web.HTTPConflict(text="Invalid browser session transition") from None
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid expected_version") from None
    except sqlite3.Error:
        raise web.HTTPServiceUnavailable(text="Browser session store unavailable") from None


def register_browser_session_routes(
    app: web.Application, *, store_path=None
) -> None:
    app[KEY] = CustomerBrowserSessionStore(
        store_path or config_dir() / "browser/customer_sessions.sqlite3"
    )
    app.router.add_post("/api/browser/sessions", create)
    app.router.add_get("/api/browser/sessions/{session_id}", get)
    app.router.add_post("/api/browser/sessions/{session_id}/{action:close|reopen}", mutate)
