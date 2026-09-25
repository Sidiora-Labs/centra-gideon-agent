"""Captured-home native shared health exchange; no arbitrary paths."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.shared_health import SharedHealthStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = SharedHealthStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            suffix = request.path.removeprefix("/api/capabilities/wellbeing/shared")
            if suffix == "/download":
                return web.Response(
                    body=await asyncio.to_thread(store.download),
                    content_type="application/json",
                    headers={
                        "Content-Disposition": 'attachment; filename="wellbeing-shared.json"'
                    },
                )
            if request.method == "GET":
                result = await asyncio.to_thread(store.status)
            else:
                payload = await read_json_body(request)
                methods = {
                    "/preview": store.preview,
                    "/commit": store.commit,
                    "/file/commit": store.commit_file,
                    "/publish": store.publish,
                }
                if suffix == "/file/preview":
                    if payload != {}:
                        raise MeasurementError("File preview accepts no overrides")
                    result = await asyncio.to_thread(store.preview_file)
                else:
                    result = await asyncio.to_thread(methods[suffix], payload)
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/shared"
    app.router.add_get(base, handle)
    app.router.add_get(base + "/download", handle)
    for suffix in ("/preview", "/commit", "/file/preview", "/file/commit", "/publish"):
        app.router.add_post(base + suffix, handle)
