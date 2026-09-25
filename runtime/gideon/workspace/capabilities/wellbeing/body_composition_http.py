"""Hosted HTTP adapter for authored body-composition observations."""

import json

from aiohttp import web

from gideon.core.config.loader import config_dir

from .body_composition import BodyCompositionStore
from .store import MeasurementError

STORE = web.AppKey("wellbeing_body_composition", BodyCompositionStore)
BASE = "/api/capabilities/wellbeing/body-composition"


async def handle(request):
    store = request.app[STORE]
    try:
        if request.query.keys() - {"from", "to", "limit", "offset"}:
            raise ValueError("Unknown query field")
        identity = request.match_info.get("id")
        if request.method in {"POST", "PUT"}:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")
            result = (
                store.create(payload)
                if request.method == "POST"
                else store.correct(identity, payload)
            )
            return web.json_response(
                result, status=201 if request.method == "POST" else 200
            )
        if request.path.endswith("/history"):
            return web.json_response({"history": store.history(identity)})
        if request.path.endswith("/export"):
            return web.json_response(store.export())
        if identity:
            return web.json_response(store.get(identity))
        return web.json_response(
            {
                "records": store.list(
                    from_date=request.query.get("from"),
                    to_date=request.query.get("to"),
                    limit=int(request.query.get("limit", "100")),
                    offset=int(request.query.get("offset", "0")),
                )
            }
        )
    except MeasurementError as error:
        return web.json_response(
            {"error": str(error), "code": error.code}, status=error.status
        )
    except (ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return web.json_response(
            {"error": "Invalid body-composition request", "code": "invalid_request"},
            status=400,
        )


def register(app, home=None):
    app[STORE] = BodyCompositionStore(
        home or app.get("capability_home") or config_dir()
    )
    app.router.add_get(BASE, handle)
    app.router.add_post(BASE, handle)
    app.router.add_get(BASE + "/export", handle)
    app.router.add_get(BASE + "/{id}", handle)
    app.router.add_put(BASE + "/{id}", handle)
    app.router.add_get(BASE + "/{id}/history", handle)
