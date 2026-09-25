"""Owner HTTP surface for broker cases; verified scanner results stay internal."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.privacy_brokers import PrivacyBrokerStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = PrivacyBrokerStore(home if home is not None else config_dir())

    async def respond(call, *args, envelope=None):
        value = await asyncio.to_thread(call, *args)
        response = web.json_response({envelope: value} if envelope else value)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def brokers(request):
        try:
            if request.method == "POST":
                return await respond(store.create_broker, await read_json_body(request))
            return await respond(store.list_brokers, envelope="brokers")
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def subject_cases(request):
        try:
            subject = request.match_info["subject"]
            if request.method == "POST":
                return await respond(
                    store.create_case, subject, await read_json_body(request)
                )
            return await respond(store.list_cases, subject, envelope="cases")
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def case(request):
        try:
            identity, action = request.match_info["id"], request.match_info.get(
                "action"
            )
            if request.method == "GET":
                method, envelope = (
                    (store.history, "history")
                    if action == "history"
                    else (
                        (store.events, "events")
                        if action == "events"
                        else (store.get_case, None)
                    )
                )
                return await respond(method, identity, envelope=envelope)
            payload = await read_json_body(request)
            method = (
                store.record_user_observation
                if action == "observe"
                else (
                    store.transition
                    if action == "transition"
                    else store.request_recheck
                )
            )
            return await respond(method, identity, payload)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/privacy"
    app.router.add_get(base + "/brokers", brokers)
    app.router.add_post(base + "/brokers", brokers)
    app.router.add_get(base + "/subjects/{subject}/broker-cases", subject_cases)
    app.router.add_post(base + "/subjects/{subject}/broker-cases", subject_cases)
    app.router.add_get(base + "/broker-cases/{id}", case)
    app.router.add_get(base + "/broker-cases/{id}/{action:history|events}", case)
    app.router.add_post(
        base + "/broker-cases/{id}/{action:observe|transition|recheck}", case
    )
