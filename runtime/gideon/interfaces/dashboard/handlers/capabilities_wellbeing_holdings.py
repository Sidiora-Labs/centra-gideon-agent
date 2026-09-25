"""Owner interface for private organizations, holdings and change attestations."""

import asyncio
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.privacy_holdings import OrgHoldingsStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = OrgHoldingsStore(home if home is not None else config_dir())

    async def response(call, *args, envelope=None):
        value = await asyncio.to_thread(call, *args)
        reply = web.json_response({envelope: value} if envelope else value)
        reply.headers["Cache-Control"] = "no-store"
        return reply

    async def subject_orgs(request):
        try:
            subject = request.match_info["subject"]
            if request.method == "POST":
                return await response(
                    store.create_org, subject, await read_json_body(request)
                )
            return await response(store.list_orgs, subject, envelope="organizations")
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def organization(request):
        try:
            identity = request.match_info["id"]
            suffix = request.match_info.get("suffix")
            if suffix == "history":
                return await response(store.history_org, identity, envelope="history")
            if suffix == "holdings":
                if request.method == "POST":
                    return await response(
                        store.set_holding, identity, await read_json_body(request)
                    )
                return await response(
                    store.list_holdings, identity, envelope="holdings"
                )
            if request.method == "PUT":
                return await response(
                    store.update_org, identity, await read_json_body(request)
                )
            return await response(store.get_org, identity)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def holding_history(request):
        try:
            return await response(
                store.history_holding, request.match_info["id"], envelope="history"
            )
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)

    async def subject_changes(request):
        try:
            subject = request.match_info["subject"]
            if request.method == "POST":
                return await response(
                    store.declare_change, subject, await read_json_body(request)
                )
            return await response(store.list_changes, subject, envelope="changes")
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    async def change(request):
        try:
            identity = request.match_info["id"]
            org = request.match_info.get("org")
            if org and request.match_info.get("suffix") == "history":
                return await response(
                    store.history_target, identity, org, envelope="history"
                )
            if org:
                return await response(
                    store.settle, identity, org, await read_json_body(request)
                )
            return await response(store.get_change, identity)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error("invalid_request", message=str(exc), status=400)

    base = "/api/capabilities/wellbeing/privacy"
    app.router.add_get(base + "/subjects/{subject}/organizations", subject_orgs)
    app.router.add_post(base + "/subjects/{subject}/organizations", subject_orgs)
    app.router.add_get(base + "/organizations/{id}", organization)
    app.router.add_put(base + "/organizations/{id}", organization)
    app.router.add_get(
        base + "/organizations/{id}/{suffix:history|holdings}", organization
    )
    app.router.add_post(base + "/organizations/{id}/{suffix:holdings}", organization)
    app.router.add_get(base + "/holdings/{id}/history", holding_history)
    app.router.add_get(base + "/subjects/{subject}/changes", subject_changes)
    app.router.add_post(base + "/subjects/{subject}/changes", subject_changes)
    app.router.add_get(base + "/changes/{id}", change)
    app.router.add_post(base + "/changes/{id}/organizations/{org}", change)
    app.router.add_get(
        base + "/changes/{id}/organizations/{org}/{suffix:history}", change
    )
