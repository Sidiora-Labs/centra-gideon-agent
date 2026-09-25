"""Consumption entries, preset snapshots and calendar summaries."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.store import MeasurementError
from gideon.workspace.capabilities.wellbeing.substances import ConsumptionStore


def register(app: web.Application, home: Path | None = None):
    store = ConsumptionStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get("id")
            preset = "/presets" in request.path
            if request.method in ("POST", "PUT", "DELETE"):
                body = (
                    {
                        "request_id": request.query.get("request_id"),
                        "revision": int(request.query.get("revision", "0")),
                    }
                    if request.method == "DELETE" and request.query
                    else await read_json_body(request)
                )
                operation = {
                    ("POST", False): store.create_entry,
                    ("PUT", False): store.correct_entry,
                    ("DELETE", False): store.delete_entry,
                    ("POST", True): store.create_preset,
                    ("PUT", True): store.update_preset,
                    ("DELETE", True): store.delete_preset,
                }[(request.method, preset)]
                result = (
                    await asyncio.to_thread(operation, identity, body)
                    if identity
                    else await asyncio.to_thread(operation, body)
                )
            elif request.path.endswith("/summary"):
                result = await asyncio.to_thread(
                    store.summary,
                    timezone=request.query.get("timezone", "UTC"),
                    days=int(request.query.get("days", "30")),
                    as_of=request.query.get("as_of"),
                )
            elif request.path.endswith("/history"):
                result = {
                    "history": await asyncio.to_thread(store.history_entry, identity)
                }
            elif identity:
                result = await asyncio.to_thread(store.get_entry, identity)
            elif preset:
                result = {
                    "presets": await asyncio.to_thread(
                        store.list_presets, request.query.get("kind")
                    )
                }
            else:
                result = {
                    "entries": await asyncio.to_thread(
                        store.list_entries,
                        kind=request.query.get("kind"),
                        from_date=request.query.get("from"),
                        to_date=request.query.get("to"),
                        limit=int(request.query.get("limit", "100")),
                        offset=int(request.query.get("offset", "0")),
                    )
                }
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/substances"
    for entity in ("entries", "presets"):
        app.router.add_get(base + "/" + entity, handle)
        app.router.add_post(base + "/" + entity, handle)
        app.router.add_put(base + "/" + entity + "/{id}", handle)
        app.router.add_delete(base + "/" + entity + "/{id}", handle)
    app.router.add_get(base + "/entries/{id}", handle)
    app.router.add_get(base + "/entries/{id}/history", handle)
    app.router.add_get(base + "/summary", handle)
