"""HTTP owner surface for configured quota plans and reservations."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.platform.quotas import (
    QuotaError,
    SubscriptionQuotaStore,
)


def register(app: web.Application, home: Path | None = None):
    store = SubscriptionQuotaStore(home if home is not None else config_dir())

    async def respond(call, *args, envelope=None):
        value = await asyncio.to_thread(call, *args)
        response = web.json_response({envelope: value} if envelope else value)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def plans(request):
        try:
            if request.method == "POST":
                return await respond(store.create_plan, await read_json_body(request))
            return await respond(store.list_plans, envelope="plans")
        except QuotaError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def plan(request):
        try:
            identity, action = request.match_info["id"], request.match_info.get(
                "action"
            )
            if request.method == "PUT":
                return await respond(
                    store.update_plan, identity, await read_json_body(request)
                )
            if action == "history":
                return await respond(store.history, identity, envelope="history")
            if action == "summary":
                return await respond(store.summary, identity)
            if action == "reservations":
                return await respond(
                    store.list_reservations, identity, envelope="reservations"
                )
            if action == "provider-evidence":
                return await respond(
                    store.provider_evidence, identity, envelope="evidence"
                )
            return await respond(store.get_plan, identity)
        except QuotaError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def reservations(request):
        try:
            if request.match_info.get("id"):
                return await respond(
                    store.release,
                    request.match_info["id"],
                    await read_json_body(request),
                )
            return await respond(
                store.reserve, request.match_info["plan"], await read_json_body(request)
            )
        except QuotaError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/platform/quotas"
    app.router.add_get(base + "/plans", plans)
    app.router.add_post(base + "/plans", plans)
    app.router.add_get(base + "/plans/{id}", plan)
    app.router.add_put(base + "/plans/{id}", plan)
    app.router.add_get(
        base + "/plans/{id}/{action:history|summary|reservations|provider-evidence}",
        plan,
    )
    app.router.add_post(base + "/plans/{plan}/reservations", reservations)
    app.router.add_post(base + "/reservations/{id}/release", reservations)
