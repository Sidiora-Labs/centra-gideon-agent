"""Explicit external markdown vault registration and conflict-safe editing."""

from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session, _is_restricted_session
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.external_vaults import ExternalVaults


async def operation(request):
    try:
        if request.query: raise CaptureError("Vault operations do not accept query overrides")
        state = request.app["state"]
        if _blocks_reads_session(state, request) or (request.method != "GET" and _is_restricted_session(state, request)): raise CaptureError("This session cannot access external vaults", 403)
        vaults = request.app["capability_external_vaults"]; identity = request.match_info.get("identity"); action = request.match_info.get("action", "")
        if request.path.endswith("/register"): result = vaults.register(await read_json_body(request))
        elif not identity: result = vaults.list()
        elif request.method == "DELETE" and not action: result = vaults.remove(identity)
        elif action == "scan": result = vaults.scan(identity)
        elif action == "read":
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {"path"}: raise CaptureError("Vault read requires path")
            result = vaults.detail(identity, body["path"])
        elif action == "write": result = vaults.write(identity, await read_json_body(request))
        elif action == "delete": result = vaults.delete(identity, await read_json_body(request))
        elif action == "search":
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {"query"}: raise CaptureError("Vault search requires query")
            result = vaults.search(identity, body["query"])
        elif action == "graph": result = vaults.graph(identity)
        else: raise CaptureError("Unknown vault operation", 404)
        return web.json_response(result)
    except (CaptureError, ValueError, TypeError, KeyError) as exc:
        return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))


def register(app):
    app["capability_external_vaults"] = ExternalVaults(app["state"].knowledge_store)
    root = "/api/capabilities/knowledge/vaults"
    app.router.add_get(root, operation); app.router.add_post(root + "/register", operation); app.router.add_delete(root + "/{identity}", operation)
    app.router.add_post(root + "/{identity}/{action:scan|read|write|delete|search}", operation); app.router.add_get(root + "/{identity}/{action:graph}", operation)
