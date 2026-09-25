"""Bounded local Apple export HTTP operations."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.apple_health import AppleHealthStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = AppleHealthStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            if request.method == "POST":
                body = await read_json_body(request.clone(client_max_size=12 * 1024 * 1024))
                operation = store.preview if request.path.endswith("/preview") else store.commit
                result = await asyncio.to_thread(operation, body)
            elif request.match_info.get("id"):
                data = await asyncio.to_thread(store.original_metric, request.match_info["id"])
                return web.Response(body=data, content_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="health-export.bin"'})
            else:
                result = {"metrics": await asyncio.to_thread(store.list_metrics, metric=request.query.get("metric"), unit=request.query.get("unit"), from_date=request.query.get("from"), to_date=request.query.get("to"), limit=int(request.query.get("limit", "100")), offset=int(request.query.get("offset", "0")))}
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/apple"
    app.router.add_post(base + "/import/preview", handle)
    app.router.add_post(base + "/import/commit", handle)
    app.router.add_get(base + "/metrics", handle)
    app.router.add_get(base + "/metrics/{id}/source", handle)
