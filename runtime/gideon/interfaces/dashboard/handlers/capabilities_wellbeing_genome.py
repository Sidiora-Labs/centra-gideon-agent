"""Home-scoped genome sources, variants and authored annotation history."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.genome import GenomeStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = GenomeStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get("id")
            suffix = request.path.rsplit("/", 1)[-1]
            if request.method == "POST":
                result = await asyncio.to_thread(
                    getattr(store, suffix), await read_json_body(request)
                )
            elif request.method == "PUT":
                result = await asyncio.to_thread(
                    store.annotate, identity, await read_json_body(request)
                )
            elif suffix == "original":
                raw = await asyncio.to_thread(store.original, identity)
                return web.Response(
                    body=raw,
                    content_type="application/octet-stream",
                    headers={
                        "Content-Disposition": 'attachment; filename="genome-source.txt"'
                    },
                )
            elif suffix == "history":
                result = {"history": await asyncio.to_thread(store.history, identity)}
            elif suffix == "sources":
                result = {"sources": await asyncio.to_thread(store.list_sources)}
            elif suffix == "variants":
                result = {
                    "variants": await asyncio.to_thread(
                        store.list_variants,
                        identity,
                        chromosome=request.query.get("chromosome"),
                        rsid=request.query.get("rsid"),
                        limit=int(request.query.get("limit", "100")),
                        offset=int(request.query.get("offset", "0")),
                    )
                }
            else:
                method = (
                    store.get_source
                    if "/sources/" in request.path
                    else store.get_variant
                )
                result = await asyncio.to_thread(method, identity)
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/genome"
    for path in ("preview", "commit"):
        app.router.add_post(base + "/" + path, handle)
    for path in (
        "sources",
        "sources/{id}",
        "sources/{id}/original",
        "sources/{id}/variants",
        "variants/{id}",
        "variants/{id}/history",
    ):
        app.router.add_get(base + "/" + path, handle)
    app.router.add_put(base + "/variants/{id}/annotation", handle)
