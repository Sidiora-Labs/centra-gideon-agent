"""`POST /mcp` — JSON-RPC 2.0 over HTTP (MCP-READONLY-INBOUND §C2).

The minimum an MCP client needs to connect and enumerate: `initialize`,
`tools/list`, `tools/call`. Deliberately NOT implemented:

* **No SSE stream** (`GET /mcp` → 405). The spec permits a POST-only server, and a
  long-lived stream is a second lifecycle to get right for no v1 benefit.
* **No batch requests.** A batch multiplies one authenticated request into N
  handler invocations, which complicates every cap (rate, deadline, result size).
  Refused with a clear JSON-RPC error rather than half-supported.

Order of checks matters and is deliberate: enablement → peer → token → rate →
concurrency → body size → parse. Each rejects with the least information that
still lets a legitimate client fix its call, and every rejection is audited.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from aiohttp import web

from gideon.core.constants import JSONRPC_METHOD_NOT_FOUND
from gideon.http_errors import json_error
from gideon.integrations.inbound import audit as audit_mod
from gideon.integrations.inbound import auth
from gideon.integrations.inbound import caps as caps_mod

logger = logging.getLogger(__name__)

SURFACE = "mcp"

_NO_STORE = {"Cache-Control": "no-store"}

PROTOCOL_VERSION = "2025-06-18"

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2024-11-05")

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = JSONRPC_METHOD_NOT_FOUND
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _rpc_error(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _rpc_result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json(payload: dict, status: int = 200) -> web.Response:
    """Results and JSON-RPC frames. Every HTTP-level refusal goes through `json_error`.

    JSON-RPC error frames still travel through here, deliberately: they are a *different*
    envelope on a different layer — an integer `code` from the spec's reserved range,
    carried at HTTP 200 — and merging them into the wire vocabulary would claim a
    transport failure where the transport succeeded.

    What no longer travels through here is the HTTP-level ``{"error": "<prose>"}``
    refusal. Seven of them did, and because the payload is a *variable* by the time it
    reaches `json_response`, `tests/test_wire_error_envelope_census.py` scored this
    module at ZERO on both of its rails until it was widened to follow wrapper
    indirection — the same blindness that hid eleven in `inbound/bridge.py`. A local
    response wrapper is exactly where an error shape hides, so this one is now
    refusal-free by construction and the codes are literals at their call sites.
    """
    return web.json_response(payload, status=status, headers=_NO_STORE)


def enablement_problem() -> str | None:
    """Why the surface must not mount, or None when it may.

    Delegates to the shared admission gate (`gate.surface_enablement_problem`), which
    owns the master + per-surface + token layers for all five surfaces. Kept as a
    named function because `mount()` and the tests both call it, and because a
    dialect asking "may I serve?" should not have to know how many layers there are.
    """
    from gideon.integrations.inbound.gate import surface_enablement_problem

    return surface_enablement_problem(SURFACE)


async def handle_mcp_get(request: web.Request) -> web.Response:
    """`GET /mcp` → 405. No SSE stream in v1 (spec-permitted)."""
    audit_mod.audit(SURFACE, route="GET /mcp", status=405, refused="GET not supported")
    return json_error(
        "method_not_allowed",
        message="This MCP surface is POST-only (no SSE stream). Use POST /mcp.",
        status=405,
        headers=_NO_STORE,
    )


async def handle_mcp(request: web.Request) -> web.Response:
    from gideon.integrations.inbound import clients as clients_mod
    from gideon.integrations.inbound.gate import admission_problem

    started = time.monotonic()
    bytes_in = 0
    client_id = ""

    def _done(
        status: int,
        payload: dict,
        refused: str = "",
        tool: str = "",
        rate_limited: bool = False,
    ) -> web.Response:
        body = json.dumps(payload)
        audit_mod.audit(
            SURFACE,
            route="POST /mcp",
            status=status,
            bytes_in=bytes_in,
            bytes_out=len(body),
            duration_ms=int((time.monotonic() - started) * 1000),
            refused=refused,
            tool=tool,
            client_id=client_id,
            rate_limited=rate_limited,
        )
        return _json(payload, status=status)

    def _refuse(
        response: web.Response,
        refused: str = "",
        tool: str = "",
        rate_limited: bool = False,
    ) -> web.Response:
        """`_done` for an HTTP-level refusal: the response is already BUILT.

        Takes the finished `json_error` response rather than a code, so that every
        ``code`` stays a string LITERAL at its own call site. A helper with a
        ``code: str`` parameter would read as tidier and be strictly worse: the
        emitter call inside it would be `json_error(code, ...)`, one dynamic site
        that the append-only registry check cannot inspect at all — and the census's
        dynamic-code bucket is already at its ceiling. The tidier shape converts nine
        countable sites into one uncountable one.

        Status and byte count are read off the response instead of being passed
        alongside it, so the audit row cannot disagree with what went on the wire.
        `content_length` rather than ``len(response.body)``: `body` is typed
        ``bytes | bytearray | Payload`` and a `Payload` has no length, whereas
        `content_length` is the number aiohttp will actually put on the wire.
        """
        audit_mod.audit(
            SURFACE,
            route="POST /mcp",
            status=response.status,
            bytes_in=bytes_in,
            bytes_out=response.content_length or 0,
            duration_ms=int((time.monotonic() - started) * 1000),
            refused=refused,
            tool=tool,
            client_id=client_id,
            rate_limited=rate_limited,
        )
        return response

    problem, status = admission_problem(SURFACE)
    if problem:
        if status == 503:
            return _refuse(
                json_error("service_unavailable", status=503, headers=_NO_STORE),
                refused=problem,
            )
        return _refuse(
            json_error("not_found", status=404, headers=_NO_STORE), refused=problem
        )

    peer_ok, peer_reason = auth.peer_allowed(request, SURFACE)
    if not peer_ok:
        return _refuse(
            json_error("forbidden", status=403, headers=_NO_STORE), refused=peer_reason
        )

    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :].strip()

    client, client_reason = clients_mod.lookup_by_token(presented, SURFACE)
    if client is not None:
        client_id = client.client_id
    elif not auth.verify_bearer(SURFACE, presented):
        return _refuse(
            json_error("unauthorized", status=401, headers=_NO_STORE),
            refused=client_reason or "bad or missing bearer token",
        )

    peer_fallback = request.headers.get("Host", "") + "|" + (request.remote or "")
    caps = caps_mod.caps_for(client)
    if not caps_mod.check_rate_for_client(SURFACE, client_id, peer_fallback, caps):
        _record_breach(client_id, "rate limit")
        return _refuse(
            json_error(
                "rate_limited",
                status=429,
                headers={
                    **_NO_STORE,
                    "Retry-After": str(
                        caps_mod.retry_after_for_client(
                            SURFACE, client_id, peer_fallback, caps
                        )
                    ),
                },
            ),
            refused="rate limit",
            rate_limited=True,
        )

    slot = caps_mod.slot_key(SURFACE, client_id, peer_fallback)
    if not caps_mod.acquire_slot(slot, caps):
        _record_breach(client_id, "concurrency cap")
        return _refuse(
            json_error("too_many_concurrent_requests", status=503, headers=_NO_STORE),
            refused="concurrency cap",
        )

    if client_id:
        clients_mod.touch_last_seen(client_id)

    try:
        declared = request.content_length or 0
        if declared > caps_mod.DEFAULT_CAPS.body_bytes:
            return _refuse(
                json_error("request_too_large", status=413, headers=_NO_STORE),
                refused="body cap (declared)",
            )
        raw = await request.content.read(caps_mod.DEFAULT_CAPS.body_bytes + 1)
        bytes_in = len(raw)
        if bytes_in > caps_mod.DEFAULT_CAPS.body_bytes:
            return _refuse(
                json_error("request_too_large", status=413, headers=_NO_STORE),
                refused="body cap",
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _done(
                200,
                _rpc_error(None, _PARSE_ERROR, "invalid JSON"),
                refused="parse error",
            )

        if isinstance(payload, list):
            return _done(
                200,
                _rpc_error(None, _INVALID_REQUEST, "batch requests are not supported"),
                refused="batch rejected",
            )
        if not isinstance(payload, dict):
            return _done(
                200,
                _rpc_error(None, _INVALID_REQUEST, "request must be a JSON-RPC object"),
                refused="non-object request",
            )

        request_id = payload.get("id")
        method = str(payload.get("method", ""))
        params: Any = payload.get("params") or {}
        if not isinstance(params, dict):
            return _done(
                200,
                _rpc_error(request_id, _INVALID_PARAMS, "params must be an object"),
                refused="params not an object",
            )

        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested is not None and str(requested) in SUPPORTED_PROTOCOL_VERSIONS:
                negotiated = str(requested)
            else:
                negotiated = PROTOCOL_VERSION
                if requested is not None:
                    logger.info(
                        "inbound: mcp initialize requested unsupported protocolVersion %r; "
                        "counter-offered %s (this server speaks %s)",
                        str(requested),
                        negotiated,
                        ", ".join(SUPPORTED_PROTOCOL_VERSIONS),
                    )
            return _done(
                200,
                _rpc_result(
                    request_id,
                    {
                        "protocolVersion": negotiated,
                        "capabilities": {"tools": {}},
                        "serverInfo": _server_info(),
                    },
                ),
            )

        if method == "tools/list":
            from gideon.integrations.inbound.tools import list_tools

            listed = list_tools()
            if client is not None:
                permitted = set(
                    clients_mod.allowed_tools(
                        client, [str(t.get("name", "")) for t in listed]
                    )
                )
                listed = [t for t in listed if str(t.get("name", "")) in permitted]
            return _done(200, _rpc_result(request_id, {"tools": listed}))

        if method == "tools/call":
            from gideon.integrations.inbound.tools import call_tool

            name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _done(
                    200,
                    _rpc_error(
                        request_id, _INVALID_PARAMS, "arguments must be an object"
                    ),
                    refused="arguments not an object",
                    tool=name,
                )
            if client is not None:
                violation = clients_mod.check_bindings(client, arguments)
                if not violation and client.tools and name not in client.tools:
                    violation = (
                        f"client {client.client_id} is pinned to tools "
                        f"{sorted(client.tools)}; request called un-bound {name!r}"
                    )
                if violation:
                    clients_mod.log_binding_violation(client.client_id, violation)
                    return _refuse(
                        json_error(
                            "forbidden",
                            status=403,
                            headers=_NO_STORE,
                            error_extra={
                                "detail": "request conflicts with a client binding"
                            },
                        ),
                        refused=violation,
                        tool=name,
                    )
            try:
                result = await call_tool(
                    name, arguments, request.app.get("state"), client_id
                )
            except KeyError:
                return _done(
                    200,
                    _rpc_error(request_id, _METHOD_NOT_FOUND, f"unknown tool {name!r}"),
                    refused="unknown tool",
                    tool=name,
                )
            except ValueError as exc:
                return _done(
                    200,
                    _rpc_error(request_id, _INVALID_PARAMS, str(exc)),
                    refused="invalid arguments",
                    tool=name,
                )
            except Exception:  # noqa: BLE001
                logger.warning("inbound: tool %r failed", name, exc_info=True)
                return _done(
                    200,
                    _rpc_error(request_id, _INTERNAL_ERROR, "tool execution failed"),
                    tool=name,
                )
            return _done(200, _rpc_result(request_id, result), tool=name)

        if request_id is None:
            return _done(200, {"jsonrpc": "2.0", "result": None, "id": None})
        return _done(
            200,
            _rpc_error(request_id, _METHOD_NOT_FOUND, f"unknown method {method!r}"),
            refused=f"unknown method {method!r}",
        )
    finally:
        caps_mod.release_slot(slot)


def _record_breach(client_id: str, reason: str) -> None:
    """Count one cap breach toward auto-disable (§1.3). No-op for an anonymous caller.

    Read from config here rather than baked in, so the owner's
    `auto_disable_after_breaches` (including 0 = never) governs.
    """
    if not client_id:
        return
    try:
        from gideon.core.config.loader import AppConfig
        from gideon.integrations.inbound import clients as clients_mod

        limit = int(AppConfig.load().external_access.auto_disable_after_breaches)
        clients_mod.record_breach(client_id, limit=limit, reason=reason)
    except Exception:  # noqa: BLE001 — bookkeeping must not fail the response
        logger.debug("inbound: breach bookkeeping failed", exc_info=True)


def _server_info() -> dict:
    try:
        from gideon import __version__

        version = __version__
    except Exception:  # noqa: BLE001
        version = "0"
    return {"name": "gideon", "version": version}


def mount(app: web.Application) -> bool:
    """Mount `/mcp` when enablement passes. Returns whether it mounted.

    A refusal logs ONE line naming the failing condition — "inbound disabled" with
    no cause is the kind of message that costs an hour of debugging.
    """
    problem = enablement_problem()
    if problem:
        logger.info("inbound: /mcp NOT mounted — %s", problem)
        return False
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/mcp", handle_mcp_get)
    logger.info(
        "inbound: /mcp mounted (loopback-only unless allow_remote + public_url)"
    )
    return True
