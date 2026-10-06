"""Owner review of the exact definitions advertised by an allowed MCP server."""

import hashlib
import json
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.security import mcp_grants, mcp_read_only_trust


def catalog_revision(configuration: str, listed: dict[str, str]) -> str:
    material = json.dumps([configuration, listed], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def read_only_trust_of(server: Any, *, tools: list | None = None) -> dict | None:
    if mcp_grants.exempt(server):
        return None
    inventory = tools if tools is not None else (server.tools if server.status == "ok" else None)
    review = mcp_read_only_trust.review(server.name, inventory).as_dict()
    configuration = mcp_read_only_trust.configuration_revision(server.name)
    review["configurationRevision"] = configuration
    review["catalogRevision"] = catalog_revision(configuration, review["listed"]) if review["listed"] is not None else None
    return review


async def api_mcp_server_read_only_trust(request: web.Request) -> web.Response:
    from gideon.integrations.mcp_client import get_mcp_client_registry
    from gideon.integrations.mcp_discovery import list_servers
    from gideon.integrations.mcp_status import announce
    from gideon.security.owner_grants import GrantBookError

    from gideon.interfaces.dashboard.handlers.mcp import _mcp_owner
    if _mcp_owner(request) is None:
        return web.json_response({"error": "Only the authenticated owner may review MCP read-only labels"}, status=403)
    name = request.match_info["name"]
    server = next((item for item in list_servers() if item.name == name), None)
    if server is None:
        return web.json_response({"error": "MCP server not found"}, status=404)
    if mcp_grants.exempt(server):
        return web.json_response({"error": "Native tools retain their own authority"}, status=400)
    registry = get_mcp_client_registry()
    if request.method == "DELETE":
        try:
            mcp_read_only_trust.revoke(name)
        except GrantBookError:
            return web.json_response({"error": "The trust record cannot be safely updated"}, status=409)
        if registry is not None:
            registry.invalidate_server(name, serving_only=True)
        announce(name)
        return web.json_response({"ok": True, "trusted": False})
    body = await read_json_body(request)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    seen = body.get("tools")
    if not isinstance(seen, dict) or len(seen) > 1000 or not all(isinstance(name, str) and mcp_read_only_trust.is_digest(digest) for name, digest in seen.items()):
        return web.json_response({"error": "tools must contain reviewed tool names and definition digests"}, status=400)
    if body.get("confirm") is not True:
        return web.json_response({"error": "Confirm trusting only the reviewed tools' read-only labels"}, status=400)
    configuration = mcp_read_only_trust.configuration_revision(name)
    if not configuration or body.get("configurationRevision") != configuration or body.get("catalogRevision") != catalog_revision(configuration, seen):
        return web.json_response({"error": "This server or its reviewed inventory changed. Refresh and review again."}, status=409)
    if not mcp_grants.allowed(server):
        return web.json_response({"error": "Allow the server definition before reviewing its tools"}, status=403)
    connection = registry.get(name) if registry is not None else None
    if connection is None:
        return web.json_response({"error": "The MCP tool connection is unavailable"}, status=503)
    try:
        tools = await connection.list_tools()
        current = {tool.name: mcp_read_only_trust.digest(tool) for tool in tools}
        if configuration != mcp_read_only_trust.configuration_revision(name) or any(current.get(tool) != digest for tool, digest in seen.items()):
            return web.json_response({"error": "The reviewed definitions changed. Refresh and review again."}, status=409)
        sealed = mcp_read_only_trust.seal(name, tools, seen, configuration=configuration)
    except (ValueError, GrantBookError) as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except Exception:
        return web.json_response({"error": "The server could not list its current tools"}, status=502)
    registry.invalidate_server(name, serving_only=True)
    announce(name)
    return web.json_response({"ok": True, "sealed": list(sealed.sealed), "changedSince": list(sealed.changed_since)})
