"""Local exercise sessions with captured-home ownership."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.cognition import CognitiveStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = CognitiveStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get("id")
            if request.method == "POST":
                body = await read_json_body(request)
                method = (
                    store.start
                    if not identity
                    else (
                        store.answer
                        if request.path.endswith("/answers")
                        else store.cancel
                    )
                )
                result = (
                    await asyncio.to_thread(method, identity, body)
                    if identity
                    else await asyncio.to_thread(method, body)
                )
            elif identity:
                result = await asyncio.to_thread(store.get, identity)
            else:
                result = {
                    "sessions": await asyncio.to_thread(
                        store.list_sessions, int(request.query.get("limit", "100"))
                    )
                }
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/cognition/sessions"
    app.router.add_get(base, handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + "/{id}", handle)
    app.router.add_post(base + "/{id}/answers", handle)
    app.router.add_post(base + "/{id}/cancel", handle)
