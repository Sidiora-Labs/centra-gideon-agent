"""Authenticated dashboard reads of operator-configured MCP resources and prompts."""

from __future__ import annotations

from aiohttp import web

from gideon.integrations.mcp_client import get_mcp_client_registry


async def _call(request: web.Request, method: str, **params) -> web.Response:
    registry = get_mcp_client_registry()
    name = request.match_info.get("name", "")
    conn = registry.get(name, "dashboard") if registry is not None else None
    if conn is None:
        return web.json_response({"error": "MCP server is unavailable"}, status=404)
    try:
        data = await conn.protocol_call(method, **params)
    except Exception as exc:
        return web.json_response({"error": str(exc)[:300]}, status=502)
    return web.json_response(data, headers={"Cache-Control": "no-store"})


async def resources_list(request: web.Request) -> web.Response:
    return await _call(request, "resources/list", cursor=request.query.get("cursor"))


async def resource_read(request: web.Request) -> web.Response:
    uri = request.query.get("uri", "")
    if not uri:
        return web.json_response({"error": "uri required"}, status=400)
    return await _call(request, "resources/read", uri=uri)


async def prompts_list(request: web.Request) -> web.Response:
    return await _call(request, "prompts/list", cursor=request.query.get("cursor"))


async def prompt_get(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    name = body.get("name") if isinstance(body, dict) else None
    arguments = body.get("arguments") or {} if isinstance(body, dict) else {}
    if not isinstance(name, str) or not name or not isinstance(arguments, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in arguments.items()):
        return web.json_response({"error": "name and string arguments required"}, status=400)
    return await _call(request, "prompts/get", name=name, arguments=arguments)
