"""Laboratory import and correction routes bound to one application home."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = LabStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get("id")
            if request.method == "POST":
                body = await read_json_body(request)
                operation = store.preview if request.path.endswith("/preview") else store.commit
                result = await asyncio.to_thread(operation, body)
            elif request.method == "PUT":
                result = await asyncio.to_thread(store.correct, identity, await read_json_body(request))
            elif request.path.endswith("/source"):
                data = await asyncio.to_thread(store.original, identity)
                return web.Response(body=data, content_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="laboratory-source.txt"'})
            elif request.path.endswith("/trends"):
                result = {"records": await asyncio.to_thread(store.trends, request.query.get("analyte"), request.query.get("unit"))}
            elif request.path.endswith("/history"):
                result = {"history": await asyncio.to_thread(store.history, identity)}
            elif identity:
                result = await asyncio.to_thread(store.get, identity)
            else:
                result = {"records": await asyncio.to_thread(store.list, analyte=request.query.get("analyte"), from_date=request.query.get("from"), to_date=request.query.get("to"), limit=int(request.query.get("limit", "100")), offset=int(request.query.get("offset", "0")))}
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    prefix = "/api/capabilities/wellbeing/labs"
    app.router.add_post(prefix + "/import/preview", handle)
    app.router.add_post(prefix + "/import/commit", handle)
    app.router.add_get(prefix + "/trends", handle)
    app.router.add_get(prefix, handle)
    app.router.add_get(prefix + "/{id}/history", handle)
    app.router.add_get(prefix + "/{id}/source", handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_put(prefix + "/{id}", handle)
