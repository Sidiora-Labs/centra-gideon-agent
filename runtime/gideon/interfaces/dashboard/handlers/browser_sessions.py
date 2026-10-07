"""Authenticated customer browser reservation and metadata routes."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import cast
from urllib.parse import quote

from aiohttp import web

from gideon.core.config import config_dir
from gideon.core.constants import DASHBOARD_SESSION_PREFIX, dashboard_session_key
from gideon.core.http_request import read_json_body
from gideon.integrations.browse.customer_control import (
    ControlDenied,
    CustomerBrowserControl,
)
from gideon.integrations.browse.customer_engine import BrowserUnavailable
from gideon.integrations.browse.customer_sessions import (
    CustomerBrowserSessionStore,
    InvalidSessionTransition,
    SessionNotFound,
    StaleSessionVersion,
)
from gideon.interfaces.dashboard.chat_utils import candidate_history_keys

KEY = web.AppKey("customer_browser_sessions", CustomerBrowserSessionStore)
CONTROL_KEY = web.AppKey("customer_browser_control", CustomerBrowserControl)
_LOCAL_ACCOUNT = "local"


def _owner(request: web.Request) -> str:
    owner = request.get("user")
    if not isinstance(owner, str) or not owner.strip() or request.get("app"):
        raise web.HTTPUnauthorized(text="Authenticated customer required")
    return owner


def _canonical_conversation(
    request: web.Request, conversation_id: str
) -> tuple[str, str] | None:
    state = request.app["state"]
    log = state.conversation_log
    if log is not None:
        for key in candidate_history_keys(conversation_id):
            if not log.has_log(key):
                continue
            meta = log.get_metadata(key)
            if (
                not meta
                or meta.get("app")
                or meta.get("memory_mode", "persistent") != "persistent"
                or meta.get("closed")
            ):
                return None
            if key.startswith(DASHBOARD_SESSION_PREFIX):
                name = key.removeprefix(DASHBOARD_SESSION_PREFIX)
                public_id = key if log.has_log(name) else name
            else:
                public_id = key
            return key, public_id
    name = conversation_id.removeprefix(DASHBOARD_SESSION_PREFIX)
    session = state.get_session(name)
    if session is not None and (
        getattr(session, "memory_mode", "persistent") == "persistent"
        and not getattr(session, "_app", "")
    ):
        return dashboard_session_key(name), name
    return None


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
        {"session": session.public()},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


async def create(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    conversation_id = body.get("conversation_id")
    if (
        not isinstance(conversation_id, str)
        or not conversation_id
        or len(conversation_id) > 255
    ):
        raise web.HTTPBadRequest(text="conversation_id is required")
    store = request.app[KEY]
    try:
        canonical = _canonical_conversation(request, conversation_id)
        if canonical is None:
            raise SessionNotFound
        canonical_key, public_id = canonical
        existing = await asyncio.to_thread(
            store.find_conversation, _LOCAL_ACCOUNT, owner, canonical_key
        )
        if existing is not None:
            return _reply(existing)
        session, created = await asyncio.to_thread(
            store.reserve, _LOCAL_ACCOUNT, owner, public_id, canonical_key
        )
        return _reply(session, status=201 if created else 200)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except (sqlite3.Error, OSError):
        raise web.HTTPServiceUnavailable(
            text="Browser session store unavailable"
        ) from None


async def get(request: web.Request) -> web.Response:
    owner = _owner(request)
    try:
        session = await request.app[CONTROL_KEY].state(
            request.match_info["session_id"],
            _LOCAL_ACCOUNT,
            owner,
        )
        return _reply(session)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except sqlite3.Error:
        raise web.HTTPServiceUnavailable(
            text="Browser session store unavailable"
        ) from None


async def mutate(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    try:
        action = request.match_info["action"]
        if action == "close":
            session = await request.app[CONTROL_KEY].close(
                request.match_info["session_id"],
                _LOCAL_ACCOUNT,
                owner,
                cast(int, body.get("expected_version")),
            )
        else:
            session = await asyncio.to_thread(
                request.app[KEY].transition,
                request.match_info["session_id"],
                _LOCAL_ACCOUNT,
                owner,
                expected_version=cast(int, body.get("expected_version")),
                action=action,
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
        raise web.HTTPServiceUnavailable(
            text="Browser session store unavailable"
        ) from None


async def start(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    try:
        session = await request.app[CONTROL_KEY].start(
            request.match_info["session_id"],
            _LOCAL_ACCOUNT,
            owner,
            cast(int, body.get("expected_version")),
        )
        return _reply(session)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except StaleSessionVersion:
        raise web.HTTPConflict(text="Stale browser session version") from None
    except InvalidSessionTransition:
        raise web.HTTPConflict(text="Invalid browser session transition") from None
    except BrowserUnavailable:
        raise web.HTTPServiceUnavailable(text="Browser engine unavailable") from None
    except (sqlite3.Error, OSError):
        raise web.HTTPServiceUnavailable(
            text="Browser session store unavailable"
        ) from None


async def preview(request: web.Request) -> web.Response:
    owner = _owner(request)
    try:
        session, (image, url, title, stamp) = await request.app[CONTROL_KEY].preview(
            request.match_info["session_id"],
            _LOCAL_ACCOUNT,
            owner,
        )
        return web.Response(
            body=image,
            content_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Browser-Version": str(session.version),
                "X-Browser-Control": session.control_holder,
                "X-Browser-Timestamp": str(stamp),
                "X-Browser-Url": quote(url, safe=""),
                "X-Browser-Title": quote(title, safe=""),
            },
        )
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except BrowserUnavailable:
        raise web.HTTPServiceUnavailable(text="Browser preview unavailable") from None


async def control(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    action = request.match_info["action"]
    try:
        session = await request.app[CONTROL_KEY].change_holder(
            request.match_info["session_id"],
            _LOCAL_ACCOUNT,
            owner,
            cast(int, body.get("expected_version")),
            action,
        )
        return _reply(session)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except (StaleSessionVersion, InvalidSessionTransition):
        raise web.HTTPConflict(text="Stale browser control") from None
    except BrowserUnavailable:
        raise web.HTTPServiceUnavailable(text="Browser engine unavailable") from None
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid expected_version") from None


async def drive(request: web.Request) -> web.Response:
    owner = _owner(request)
    body = await _body(request)
    controller = request.app[CONTROL_KEY]
    session_id = request.match_info["session_id"]
    try:
        if request.match_info["action"] == "navigate":
            url = body.get("url")
            if not isinstance(url, str) or len(url) > 4096:
                raise ValueError
            session = await controller.navigate(
                session_id,
                _LOCAL_ACCOUNT,
                owner,
                cast(int, body.get("expected_version")),
                url,
            )
        else:
            command, value = body.get("command"), body.get("value")
            if not isinstance(command, str) or not isinstance(value, str):
                raise ValueError
            session = await controller.input(
                session_id,
                _LOCAL_ACCOUNT,
                owner,
                cast(int, body.get("expected_version")),
                command,
                value,
            )
        return _reply(session)
    except SessionNotFound:
        raise web.HTTPNotFound(text="Browser session not found") from None
    except (StaleSessionVersion, InvalidSessionTransition, ControlDenied):
        raise web.HTTPConflict(text="Browser input rejected") from None
    except BrowserUnavailable:
        raise web.HTTPServiceUnavailable(text="Browser engine unavailable") from None
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid browser input") from None


def register_browser_session_routes(app: web.Application, *, store_path=None) -> None:
    app[KEY] = CustomerBrowserSessionStore(
        store_path or config_dir() / "browser/customer_sessions.sqlite3"
    )
    app[CONTROL_KEY] = CustomerBrowserControl(
        app[KEY],
        (Path(store_path).parent if store_path else config_dir() / "browser")
        / "profiles",
    )

    async def cleanup(_app: web.Application) -> None:
        await _app[CONTROL_KEY].shutdown()

    app.on_cleanup.append(cleanup)
    app.router.add_post("/api/browser/sessions", create)
    app.router.add_get("/api/browser/sessions/{session_id}", get)
    app.router.add_post(
        "/api/browser/sessions/{session_id}/{action:close|reopen}", mutate
    )
    app.router.add_post("/api/browser/sessions/{session_id}/start", start)
    app.router.add_get("/api/browser/sessions/{session_id}/preview", preview)
    app.router.add_post(
        "/api/browser/sessions/{session_id}/control/{action:takeover|handback}", control
    )
    app.router.add_post(
        "/api/browser/sessions/{session_id}/{action:navigate|input}", drive
    )
