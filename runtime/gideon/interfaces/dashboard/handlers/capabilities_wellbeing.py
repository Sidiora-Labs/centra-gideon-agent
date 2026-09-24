"""Measurement HTTP boundary, bound to one configured home per application."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.store import MeasurementError, MeasurementStore


def register(app: web.Application, home: Path | None = None):
    store = MeasurementStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get("id")
            if request.method in ("POST", "PUT"):
                body = await read_json_body(request)
                value = await asyncio.to_thread(store.correct, identity, body) if identity else await asyncio.to_thread(store.create, body)
            elif request.path.endswith("/export"):
                value = await asyncio.to_thread(store.export)
            elif request.path.endswith("/history"):
                value = {"history": await asyncio.to_thread(store.history, identity)}
            elif identity:
                value = await asyncio.to_thread(store.get, identity)
            else:
                value = {"measurements": await asyncio.to_thread(store.list, from_date=request.query.get("from"), to_date=request.query.get("to"), kind=request.query.get("kind"), limit=int(request.query.get("limit", "100")), offset=int(request.query.get("offset", "0")))}
            return web.json_response(value)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    prefix = "/api/capabilities/wellbeing"
    app.router.add_get(prefix + "/export", handle)
    app.router.add_get(prefix + "/measurements", handle)
    app.router.add_post(prefix + "/measurements", handle)
    app.router.add_get(prefix + "/measurements/{id}", handle)
    app.router.add_put(prefix + "/measurements/{id}", handle)
    app.router.add_get(prefix + "/measurements/{id}/history", handle)
