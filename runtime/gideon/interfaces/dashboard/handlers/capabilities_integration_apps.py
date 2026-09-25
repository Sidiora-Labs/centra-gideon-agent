"""Owner-reviewed Jira, Datadog and GitHub integration application routes."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.music.store import DomainError
from gideon.workspace.capabilities.platform.integration_apps.store import (
    IntegrationApps,
)

PREFIX = "/api/capabilities/platform/integration-apps"


def _store(request):
    factory = request.app.get("integration_apps_factory")
    return factory() if factory else IntegrationApps(config_dir())


def _owner(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")


async def overview(request):
    _owner(request)
    store = _store(request)
    return web.json_response(
        {"connections": store.connections(), "runs": store.runs()},
        headers={"Cache-Control": "no-store"},
    )


async def connection(request):
    _owner(request)
    try:
        body = await read_json_body(request)
        result = _store(request).save_connection(body, request.match_info.get("id"))
        return web.json_response(
            result, status=201 if request.method == "POST" else 200
        )
    except DomainError as error:
        return web.json_response(
            {"error": str(error), "code": error.code}, status=error.status
        )


async def prepare(request):
    _owner(request)
    try:
        return web.json_response(
            _store(request).prepare(
                request.match_info["id"], await read_json_body(request)
            ),
            status=201,
        )
    except DomainError as error:
        return web.json_response(
            {"error": str(error), "code": error.code}, status=error.status
        )


async def run(request):
    _owner(request)
    try:
        store = _store(request)
        result = (
            await store.execute(
                request.match_info["run"], await read_json_body(request)
            )
            if request.method == "POST"
            else store.get(request.match_info["run"])
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except DomainError as error:
        return web.json_response(
            {"error": str(error), "code": error.code}, status=error.status
        )


async def repositories(request):
    _owner(request)
    try:
        store = _store(request)
        if request.method == "GET":
            result = store.repositories(request.match_info["id"])
        else:
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {
                "name",
                "revision",
                "flags",
                "secret_refs",
            }:
                raise DomainError("Repository annotation fields required")
            result = store.annotate(request.match_info["id"], body.pop("name"), body)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except DomainError as error:
        return web.json_response(
            {"error": str(error), "code": error.code}, status=error.status
        )


def register(app):
    app.router.add_get(PREFIX, overview, name="integration-apps-overview")
    app.router.add_post(
        PREFIX + "/connections", connection, name="integration-apps-create"
    )
    app.router.add_put(
        PREFIX + "/connections/{id}", connection, name="integration-apps-update"
    )
    app.router.add_post(
        PREFIX + "/connections/{id}/prepare", prepare, name="integration-apps-prepare"
    )
    app.router.add_get(PREFIX + "/runs/{run}", run, name="integration-apps-run")
    app.router.add_post(
        PREFIX + "/runs/{run}/execute", run, name="integration-apps-execute"
    )
    app.router.add_get(
        PREFIX + "/connections/{id}/repositories",
        repositories,
        name="integration-apps-repositories",
    )
    app.router.add_put(
        PREFIX + "/connections/{id}/repositories",
        repositories,
        name="integration-apps-annotate",
    )
