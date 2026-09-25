from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error

from .lifestyle_profile import LifestyleProfileStore
from .store import MeasurementError

STORE = web.AppKey("wellbeing_lifestyle_profiles", LifestyleProfileStore)


def register(app, home=None, store=None):
    service = store or LifestyleProfileStore(home if home is not None else config_dir())
    app[STORE] = service

    async def collection(request):
        try:
            if request.query:
                raise RequestValidationError("Lifestyle list accepts no query fields")
            if request.method == "POST":
                response = web.json_response(
                    service.create(await read_json_body(request)), status=201
                )
            else:
                response = web.json_response({"records": service.list()})
            response.headers["Cache-Control"] = "no-store"
            return response
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def member(request):
        try:
            identity = request.match_info["id"]
            if request.method == "PUT":
                value = service.correct(identity, await read_json_body(request))
            elif request.path.endswith("/history"):
                value = {"history": service.history(identity)}
            else:
                value = service.get(identity)
            return web.json_response(value, headers={"Cache-Control": "no-store"})
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def export(_):
        return web.json_response(
            service.export(), headers={"Cache-Control": "no-store"}
        )

    base = "/api/capabilities/wellbeing/lifestyle-profiles"
    app.router.add_post(base, collection)
    app.router.add_get(base, collection)
    app.router.add_get(base + "/export", export)
    app.router.add_get(base + "/{id}/history", member)
    app.router.add_get(base + "/{id}", member)
    app.router.add_put(base + "/{id}", member)
