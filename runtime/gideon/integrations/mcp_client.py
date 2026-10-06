"""Long-lived in-process MCP client — external MCP servers in the native loop.

Unlike :func:`mcp_discovery.probe_server` (one-shot spawn → read → die), this
keeps a connection alive for the process and routes ``tools/list`` / ``tools/call``
over it. It is what lets Gideon's *native* agent loop invoke tools from an
external MCP server configured in ``~/.gideon/mcp.json`` — independent of
the ACP CLI backends, which spawn their own MCP servers.

Built on the official ``mcp`` Python SDK (an optional ``gideon[mcp]``
extra). The SDK's clients are async-context-manager based, so each server runs
as a small **actor**: one background task holds the transport + session context
open and serves ``list_tools`` / ``call_tool`` requests off a queue, with
health/respawn and clean shutdown (drained by the gateway's reaper on exit).

Transports: stdio (``command``/``args``/``env``) and remote SSE/HTTP (``url``).
The SDK is imported lazily so the package still imports without the extra; a
missing SDK degrades to an empty registry (no servers), never an ImportError at
module load.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import urlsplit
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from gideon.integrations.mcp_status import StartFailure

from gideon.assurance import trace_recorder as _trace

logger = logging.getLogger(__name__)

ElicitationHandler = Callable[[str, Any], Awaitable[Any]]

_CALL_TIMEOUT_SECS = 120.0
_CONNECT_TIMEOUT_SECS = 30.0
_IDLE_TTL_SECS = 600.0
_SWEEP_INTERVAL_SECS = 120.0
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_SECS = 60.0


def _resource_metadata_from_challenge(challenge: str) -> str | None:
    """Extract the resource-metadata auth parameter after its scheme token."""
    if not isinstance(challenge, str):
        return None
    parameters = re.sub(r"^\s*[A-Za-z][A-Za-z0-9+.-]*\s+", "", challenge, count=1)
    match = re.search(
        r'(?:^|,)\s*resource_metadata\s*=\s*(?:"([^"]+)"|([^,\s]+))',
        parameters,
        re.I,
    )
    if match is None:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


def _remote_http_client_factory(*, headers=None, timeout=None, auth=None, endpoint="", oauth_context=None):
    """Create MCP's HTTP client with the shared egress decision on every request."""
    import httpx

    from gideon.security.net.guard import evaluate
    from gideon.security.net.policy import MCP_SERVER, STRICT, egress_policy_for
    from gideon.security.net.client import EgressBlocked, _audit

    base = urlsplit(endpoint)
    default_headers = httpx.Headers(headers or {})
    oauth_metadata_urls: set[str] = set()

    def same_origin(candidate):
        target = urlsplit(candidate)
        if target.hostname is None or base.hostname is None:
            return False
        source_port = base.port or (443 if base.scheme == "https" else 80)
        target_port = target.port or (443 if target.scheme == "https" else 80)
        same_port = target_port == source_port or (
            base.scheme == "http" and target.scheme == "https"
            and source_port == 80 and target_port == 443
        )
        return target.hostname.lower() == base.hostname.lower() and same_port and (
            target.scheme == base.scheme or (base.scheme == "http" and target.scheme == "https")
        )

    def oauth_request(request):
        context = oauth_context
        if context is None:
            return False
        parsed = urlsplit(str(request.url))
        path = parsed.path.lower()
        if str(request.url) in oauth_metadata_urls:
            if parsed.scheme.lower() != "https":
                raise ValueError("MCP OAuth endpoints require HTTPS")
            return True
        metadata = getattr(context, "oauth_metadata", None)
        for field in ("token_endpoint", "registration_endpoint", "revocation_endpoint", "introspection_endpoint"):
            endpoint_value = getattr(metadata, field, None) if metadata is not None else None
            if endpoint_value is not None and str(endpoint_value) == str(request.url):
                if parsed.scheme.lower() != "https":
                    raise ValueError("MCP OAuth endpoints require HTTPS")
                return True
        auth_server = getattr(context, "auth_server_url", None)
        if auth_server:
            auth_origin = urlsplit(str(auth_server))
            if auth_origin.scheme.lower() != "https":
                raise ValueError("MCP OAuth authorization server requires HTTPS")
            if request.method.upper() == "POST":
                fallback_origin = f"{auth_origin.scheme}://{auth_origin.netloc}"
                if str(request.url) in {fallback_origin + "/token", fallback_origin + "/register"}:
                    if parsed.scheme.lower() != "https":
                        raise ValueError("MCP OAuth endpoints require HTTPS")
                    return True
            if request.method.upper() == "GET":
                for suffix in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
                    if str(request.url) == f"{auth_server.rstrip('/')}{suffix}":
                        return True
        return False

    def record_oauth_metadata(request, response):
        if not same_origin(str(request.url)) or response.status_code != 401:
            return
        challenge = response.headers.get("www-authenticate", "")
        candidate = _resource_metadata_from_challenge(challenge)
        if candidate:
            target = urlsplit(candidate)
            if target.scheme == "https" and target.hostname and not (target.username or target.password or target.query or target.fragment):
                oauth_metadata_urls.add(candidate)

    class GuardedTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self._transport = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            parsed = urlsplit(str(request.url))
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("MCP remote URL must not contain userinfo or a fragment")
            is_oauth = oauth_request(request)
            if not same_origin(str(request.url)) and not is_oauth:
                # Cross-origin GETs in an OAuth flow are discovery documents; credentials
                # configured for the MCP resource never travel with those requests.
                raise ValueError("MCP remote transport refused a cross-origin redirect or endpoint")
            policy = egress_policy_for(STRICT if is_oauth else MCP_SERVER)
            decision = await asyncio.to_thread(evaluate, str(request.url), policy)
            audit_origin = f"{parsed.scheme}://{parsed.netloc}"
            if not decision.allow:
                _audit(audit_origin, policy, outcome="denied", reason=decision.reason)
                raise EgressBlocked(decision)
            _audit(audit_origin, policy, outcome="allowed")
            if not policy.pin_resolved_ip or not decision.pinned_ips:
                if not is_oauth:
                    for name, value in default_headers.multi_items():
                        if name not in request.headers:
                            request.headers[name] = value
                response = await self._transport.handle_async_request(request)
                record_oauth_metadata(request, response)
                return response
            original_host = request.url.host
            original_authority = request.url.netloc.decode("ascii")
            ip = decision.pinned_ips[0]
            extensions = dict(request.extensions)
            extensions["sni_hostname"] = original_host
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=ip),
                headers=request.headers,
                stream=request.stream,
                extensions=extensions,
            )
            pinned.headers["host"] = original_authority
            if not is_oauth:
                for name, value in default_headers.multi_items():
                    if name not in pinned.headers:
                        pinned.headers[name] = value
            response = await self._transport.handle_async_request(pinned)
            response._request = request
            record_oauth_metadata(request, response)
            return response

        async def aclose(self):
            await self._transport.aclose()

    return httpx.AsyncClient(
        headers=None,
        timeout=timeout or httpx.Timeout(_CONNECT_TIMEOUT_SECS, read=300),
        auth=auth,
        transport=GuardedTransport(),
        trust_env=False,
    )


def approval_window_secs() -> float:
    return max(0.0, _CALL_TIMEOUT_SECS * 0.9)


def mcp_sdk_available() -> bool:
    """True when the optional ``mcp`` SDK is importable."""
    try:
        import mcp  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class McpToolSpec:
    """One tool advertised by a connected server (provider-neutral)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)


_INT_LITERAL_RE = re.compile(r"^-?\d+$")
_NUM_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


def _schema_numeric_kind(prop_schema: object) -> str | None:
    """Return "integer"/"number" iff ``prop_schema`` types the field as EXACTLY
    that numeric kind — i.e. it permits no string. A ``type`` that is a list
    (e.g. ``["integer", "null"]``) coerces; one that also allows ``"string"``
    (``["string", "integer"]``) does NOT. anyOf/oneOf/$ref branches are treated
    as "do not coerce" (unknown shape → leave the value alone)."""
    if not isinstance(prop_schema, dict):
        return None
    if any(k in prop_schema for k in ("anyOf", "oneOf", "allOf", "$ref")):
        return None
    t = prop_schema.get("type")
    types = {t} if isinstance(t, str) else set(t) if isinstance(t, list) and all(isinstance(item, str) for item in t) else set()
    if types - {"integer", "number", "null"}:
        return None
    if "integer" in types:
        return "integer"
    if "number" in types:
        return "number"
    return None


def _coerce_args_to_schema(
    arguments: dict[str, Any], input_schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Coerce unambiguous numeric/JSON STRING args per the tool's
    inputSchema. Only top-level properties are handled (nested object/array
    numerics are a known, accepted limitation). Non-string values, string-typed
    fields, and strings that don't strictly match a numeric literal are left
    untouched, so a genuinely bad value still reaches the server as-is (its own
    -32602 then reports the real error)."""
    if not isinstance(input_schema, dict) or not isinstance(arguments, dict):
        return arguments
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return arguments
    out: dict[str, Any] = dict(arguments)
    for key, val in arguments.items():
        if not isinstance(val, str):
            continue
        prop = props.get(key)
        kind = _schema_numeric_kind(prop)
        if isinstance(prop, dict) and not any(k in prop for k in ("anyOf", "oneOf", "allOf", "$ref")):
            declared = prop.get("type")
            types = {declared} if isinstance(declared, str) else set(declared) if isinstance(declared, list) and all(isinstance(t, str) for t in declared) else set()
            choices = types - {"null"}
            if len(choices) == 1 and choices <= {"boolean", "array", "object"}:
                try:
                    decoded = json.loads(val)
                except (ValueError, TypeError):
                    pass
                else:
                    expected = {"boolean": bool, "array": list, "object": dict}[next(iter(choices))]
                    if type(decoded) is expected:
                        out[key] = decoded
        if kind == "integer" and _INT_LITERAL_RE.match(val):
            out[key] = int(val)
        elif kind == "number" and _NUM_LITERAL_RE.match(val):
            out[key] = float(val)
    return out


@dataclass
class _Request:
    kind: str
    payload: dict[str, Any]
    answer: asyncio.Future
    sent: bool = False

    @property
    def tool(self) -> str:
        return str(self.payload.get("tool") or self.kind)


def _not_sent(request: _Request, reason: str) -> str:
    return f"MCP request '{request.tool}' was not sent: {reason}. Nothing reached the server."


def _uncertain(request: _Request, server: str, reason: str) -> str:
    return (f"MCP tool '{request.tool}' may have gone through: Gideon sent it to '{server}' and {reason}. "
            "Check whether it did what was intended before calling it again, or it may happen twice.")


class McpServerConn:
    """An actor owning one MCP server's live connection.

    The connection is established lazily on first use and held open by a
    background task. Calls are marshalled to that task so the SDK's anyio task
    scope stays on a single task (its context managers are not reentrant across
    tasks).
    """

    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        scope: str = "",
        elicitation_handler: ElicitationHandler | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        self.name = name
        self.spec = spec
        self.scope = scope
        self._task: asyncio.Task | None = None
        self._requests: asyncio.Queue[_Request] | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._tools: list[McpToolSpec] = []
        self._error: str = ""
        self._closing = False
        self._last_used: float = time.monotonic()
        self._consecutive_failures: int = 0
        self._breaker_until: float = 0.0
        self._elicitation_handler = elicitation_handler
        self._connect_timeout = connect_timeout if connect_timeout is not None else _CONNECT_TIMEOUT_SECS
        self._failure: StartFailure | None = None
        self._stdio = None
        self._credential_redactions: tuple[str, ...] = ()

    @property
    def error(self) -> str:
        return self._error

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def started(self) -> bool:
        return self._task is not None and not self._task.done()

    def touch(self) -> None:
        self._last_used = time.monotonic()

    def _definition_seal(self) -> str:
        from gideon.integrations.mcp_discovery import definition_seal

        return definition_seal(self.name, self.spec)

    async def ensure_started(self) -> bool:
        from gideon.integrations.mcp_discovery import start_refused

        self.touch()
        if self._task is None or self._task.done():
            refused = start_refused(self.name, self._definition_seal())
            if refused:
                self._error = refused
                return False
            refused = await self._egress_refusal()
            if refused:
                if not self.started:
                    self._error = refused
                return False
            if not self.started:
                self._requests = asyncio.Queue()
                self._ready = asyncio.Event()
                self._error = ""
                self._failure = None
                self._closing = False
                from gideon.security.net.policy import egress_held_to

                with egress_held_to(""):
                    self._task = asyncio.create_task(self._run(), name=f"mcp-conn-{self.name}")
        # The actor owns its startup deadline. Cancelling a waiter cannot cut off another
        # caller's startup, and a failed attempt is counted once by the actor.
        await self._ready.wait()
        return not bool(self._error)

    async def _egress_refusal(self) -> str:
        url = str(self.spec.get("url") or self.spec.get("endpoint") or "")
        if not url:
            return ""
        from gideon.security.net.client import _audit
        from gideon.security.net.guard import evaluate, refusal_for
        from gideon.security.net.policy import MCP_SERVER, egress_policy_for

        parsed = urlsplit(url)
        authority = parsed.hostname or ""
        if ":" in authority:
            authority = f"[{authority}]"
        if parsed.port:
            authority = f"{authority}:{parsed.port}"
        from gideon.integrations.mcp_secret_refs import safe_display_url

        shown = safe_display_url(url)
        policy = egress_policy_for(MCP_SERVER)
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(evaluate, url, policy), timeout=self._connect_timeout
            )
        except asyncio.TimeoutError:
            reason = "The MCP endpoint lookup did not finish; nothing was sent."
            _audit(shown, policy, outcome="denied", reason=reason)
            return reason
        if decision.allow or decision.category == "unresolvable":
            return ""
        _audit(shown, policy, outcome="denied", reason=decision.reason)
        return refusal_for(shown, decision, then="then try it again")

    def _note_failure(self) -> None:
        # Compatibility for diagnostics; startup accounting belongs to _run.
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_until = float("inf")

    async def list_tools(self) -> list[McpToolSpec]:
        if await self._egress_refusal():
            return []
        if not await self.ensure_started():
            return []
        self.touch()
        await self.protocol_call("tools/list")
        return list(self._tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any], *, expected_definition: str = "", expected_configuration: str = "", expected_read_authority: bool = False) -> tuple[bool, str]:
        if refused := await self._egress_refusal():
            return False, refused
        if not await self.ensure_started():
            return False, f"MCP server '{self.name}' not connected: {self._error}"
        self.touch()
        spec = next((t for t in self._tools if t.name == tool), None)
        if spec is not None:
            arguments = _coerce_args_to_schema(arguments, spec.input_schema)
            from gideon.integrations.tool_providers.arguments import argument_refusal
            if reason := argument_refusal(tool, arguments, spec.input_schema, types=True):
                return False, reason
        request = _Request("call", {"tool": tool, "arguments": arguments, "expected_definition": expected_definition, "expected_configuration": expected_configuration, "expected_read_authority": expected_read_authority}, asyncio.get_running_loop().create_future())
        if self._requests is None or not self.started or self._closing:
            return False, _not_sent(request, "the connection ended first")
        self._requests.put_nowait(request)
        try:
            await asyncio.wait({request.answer}, timeout=_CALL_TIMEOUT_SECS)
        finally:
            if not request.answer.done():
                request.answer.cancel()
        if request.answer.cancelled():
            answer = ((True, _uncertain(request, self.name, f"had no answer after {_CALL_TIMEOUT_SECS:g} seconds"))
                      if request.sent else (False, _not_sent(request, "its caller stopped waiting before dispatch")))
        else:
            answer = request.answer.result()
        if _trace.is_recording():
            _trace.record("mcp", self.name, "call_tool", {"tool": tool, "arguments": arguments, "ok": answer[0], "output": answer[1]})
        return answer

    async def protocol_call(self, kind: str, **payload: Any) -> dict[str, Any]:
        if refused := await self._egress_refusal():
            raise RuntimeError(refused)
        if not await self.ensure_started():
            raise RuntimeError(f"MCP server '{self.name}' not connected: {self._error}")
        self.touch()
        request = _Request(kind, payload, asyncio.get_running_loop().create_future())
        if self._requests is None or not self.started or self._closing:
            raise RuntimeError(_not_sent(request, "the connection ended first"))
        self._requests.put_nowait(request)
        try:
            return await asyncio.wait_for(request.answer, _CALL_TIMEOUT_SECS)
        finally:
            if not request.answer.done():
                request.answer.cancel()

    def _settle(self, request: _Request, value: Any = None, error: BaseException | None = None) -> None:
        if request.answer.done():
            if request.sent:
                logger.warning("MCP server '%s' answered '%s' after its caller stopped waiting", self.name, request.tool)
            return
        if error is not None:
            request.answer.set_exception(error)
        else:
            request.answer.set_result(value)

    async def shutdown(self) -> None:
        self._closing = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        """Hold the transport+session open, serve queued requests until cancelled."""
        from gideon.integrations.mcp_discovery import note_start

        requests = self._requests
        assert requests is not None
        seal = self._definition_seal()
        deadline = asyncio.timeout(self._connect_timeout)
        self._stdio = None
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession
            from mcp.client.session import ElicitationFnT
            from mcp.types import ElicitRequestParams, ElicitResult, ErrorData

            async with AsyncExitStack() as stack:
                try:
                    async with deadline:
                        read, write = await self._open_transport(stack)
                        elicitation_callback: ElicitationFnT | None = None
                        if (
                            self.spec.get("allowElicitation") is True
                            and self._elicitation_handler
                        ):

                            async def _elicit(
                                context: Any,
                                params: ElicitRequestParams,
                            ) -> ElicitResult | ErrorData:
                                handler = self._elicitation_handler
                                if handler is None:
                                    return ElicitResult(action="cancel")

                                try:
                                    return await asyncio.wait_for(
                                        handler(self.name, params),
                                        timeout=approval_window_secs(),
                                    )
                                except asyncio.TimeoutError:
                                    return ElicitResult(action="cancel")

                            elicitation_callback = _elicit
                        session = await stack.enter_async_context(
                            ClientSession(
                                read, write, elicitation_callback=elicitation_callback
                            )
                        )
                        await session.initialize()
                        await self._refresh_tools(session)
                except TimeoutError:
                    if deadline.expired() and self._stdio is not None:
                        self._stdio.stop_waiting()
                    raise
                note_start(self.name, seal, tools=self._tools)
                self._ready.set()
                await self._serve(session, requests)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._failure = self._start_failure(exc, timed_out=deadline.expired())
            self._error = self._failure.headline
            if not self._ready.is_set():
                note_start(self.name, seal, failure=self._failure)
            self._ready.set()
        finally:
            self._ready.set()
            while not requests.empty():
                request = requests.get_nowait()
                message = _not_sent(request, "the connection ended first")
                self._settle(request, (False, message) if request.kind == "call" else None,
                             None if request.kind == "call" else RuntimeError(message))

    def _start_failure(self, exc: BaseException, *, timed_out: bool) -> StartFailure:
        from gideon.integrations import mcp_status, mcp_stdio
        from gideon.integrations.mcp_discovery import _probe_error_copy

        run = self._stdio
        if run is not None:
            if run.waiting or run.left_to_finish:
                return mcp_status.still_starting(self.name, self._connect_timeout, run.stderr,
                                                allowance=mcp_stdio.FINISH_SECS, earlier=run.waiting)
            if run.exited:
                return mcp_status.exited(self.name, run.returncode, run.stderr)
            if timed_out:
                return mcp_status.did_not_answer(self.name, self._connect_timeout, run.stderr)
            if run.stopped:
                return mcp_status.closed(self.name, run.stderr)
        if isinstance(exc, mcp_stdio.CommandNotFound):
            return StartFailure(mcp_status.safe_text(str(exc)), counts=False)
        url = str(self.spec.get("url") or self.spec.get("endpoint") or "")
        from gideon.integrations.mcp_argument_secrets import scrub_text

        diagnostic = scrub_text(str(exc) or type(exc).__name__, self._credential_redactions)
        message = _probe_error_copy(diagnostic, url) if url else diagnostic
        return StartFailure(mcp_status.safe_text(message)[:500], mcp_status.safe_text(run.stderr) if run else "",
                            counts=not isinstance(exc, PermissionError))

    async def _open_transport(self, stack: Any):
        """Enter the right transport context for this server's spec."""
        from gideon.security.mcp_grants import allowed

        from gideon.integrations.mcp_discovery import list_servers, _definition_revision

        current = next((server for server in list_servers() if server.name == self.name), None)
        if current is None or _definition_revision(current) != self._definition_seal() or not allowed(current):
            raise PermissionError("MCP server is not allowed in its current definition")
        from gideon.extensions.providers.mcp_instances import resolve_server_credentials

        spec = resolve_server_credentials(self.name, self.spec)
        from gideon.integrations.mcp_argument_secrets import known_values

        self._credential_redactions = known_values(spec)
        url = spec.get("url") or spec.get("endpoint")
        if url:
            headers = spec.get("headers") or {}
            if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
                raise ValueError("MCP headers must be a string mapping")
            auth = None
            oauth_settings = spec.get("oauth")
            oauth_enabled = isinstance(oauth_settings, dict) and bool(oauth_settings)
            if not oauth_enabled and str(url).startswith("https://"):
                from gideon.integrations.mcp_oauth import McpOAuthStorage

                oauth_enabled = await McpOAuthStorage(self.name, str(url)).has_tokens()
            if oauth_enabled:
                from gideon.integrations.mcp_oauth import oauth_provider

                if not str(url).startswith("https://"):
                    raise ValueError("MCP OAuth requires HTTPS")
                auth = oauth_provider(self.name, str(url), spec)
            transport = normalize_transport(spec)
            if transport == "streamable-http":
                from mcp.client.streamable_http import streamablehttp_client

                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        url, headers=headers, auth=auth,
                        httpx_client_factory=lambda **kw: _remote_http_client_factory(
                            endpoint=str(url),
                            oauth_context=getattr(auth, "context", None),
                            **kw,
                        ),
                    )
                )
                return read, write
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(url, headers=headers, auth=auth,
                           httpx_client_factory=lambda **kw: _remote_http_client_factory(
                               endpoint=str(url),
                               oauth_context=getattr(auth, "context", None),
                               **kw,
                           ))
            )
            return read, write

        from gideon.integrations.mcp_stdio import StdioRun, stdio_streams

        self._stdio = StdioRun()
        return await stack.enter_async_context(stdio_streams(
            self.name, spec, run=self._stdio, seal=self._definition_seal(),
        ))

    async def _refresh_tools(self, session: Any) -> None:
        tools: list[McpToolSpec] = []
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            result = await session.list_tools(cursor=cursor)
            for tool in getattr(result, "tools", None) or []:
                labels = getattr(tool, "annotations", None)
                annotations = labels.model_dump(exclude_none=True) if hasattr(labels, "model_dump") else dict(labels or {})
                tools.append(McpToolSpec(name=getattr(tool, "name", ""), description=getattr(tool, "description", "") or "", input_schema=getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}, annotations=annotations))
            if len(tools) > 1000:
                raise ValueError("MCP tool inventory exceeds the review limit")
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
            if cursor in seen_cursors:
                raise ValueError("MCP tool inventory cursor repeated")
            seen_cursors.add(cursor)
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise ValueError("MCP tool inventory contains duplicate names")
        self._tools = tools
        from gideon.integrations.mcp_discovery import note_start
        note_start(self.name, self._definition_seal(), tools=tools)

    async def _serve(self, session: Any, requests: asyncio.Queue[_Request] | None = None) -> None:
        import anyio
        from mcp.shared.exceptions import McpError

        requests = requests if requests is not None else self._requests
        assert requests is not None
        while not self._closing:
            request = await requests.get()
            if request.answer.done():
                continue
            kind, payload = request.kind, request.payload
            request.sent = kind != "call"
            try:
                if kind == "tools/list":
                    await self._refresh_tools(session)
                    value = {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.input_schema, "annotations": t.annotations} for t in self._tools]}
                elif kind == "call":
                    expected = payload.get("expected_definition", "")
                    configuration = payload.get("expected_configuration", "")
                    if expected:
                        from gideon.security import mcp_read_only_trust
                        await self._refresh_tools(session)
                        current = next((t for t in self._tools if t.name == payload["tool"]), None)
                        if current is None or mcp_read_only_trust.digest(current) != expected or not configuration or mcp_read_only_trust.configuration_revision(self.name) != configuration or (payload.get("expected_read_authority") and not mcp_read_only_trust.believes(self.name, current)):
                            self._settle(request, (False, "MCP tool was not run: its reviewed definition changed. Refresh the tools and approve the current definition."))
                            continue
                    current = next((t for t in self._tools if t.name == payload["tool"]), None)
                    if current is not None:
                        from gideon.integrations.tool_providers.arguments import argument_refusal
                        payload["arguments"] = _coerce_args_to_schema(payload["arguments"], current.input_schema)
                        if reason := argument_refusal(payload["tool"], payload["arguments"], current.input_schema, types=True):
                            self._settle(request, (False, reason))
                            continue
                    request.sent = True
                    result = await session.call_tool(payload["tool"], payload["arguments"])
                    value: Any = (not getattr(result, "isError", False), _coerce_output(result))
                elif kind == "resources/list":
                    result = await session.list_resources(cursor=payload.get("cursor"))
                    value = result.model_dump(mode="json")
                elif kind == "resources/read":
                    from pydantic import AnyUrl

                    result = await session.read_resource(AnyUrl(payload["uri"]))
                    value = result.model_dump(mode="json")
                elif kind == "prompts/list":
                    result = await session.list_prompts(cursor=payload.get("cursor"))
                    value = result.model_dump(mode="json")
                elif kind == "prompts/get":
                    result = await session.get_prompt(payload["name"], payload.get("arguments") or {})
                    value = result.model_dump(mode="json")
                else:
                    raise ValueError(f"unknown request: {kind}")
            except asyncio.CancelledError:
                message = _uncertain(request, self.name, "the connection ended before its answer arrived")
                self._settle(request, (True, message) if kind == "call" else None,
                             None if kind == "call" else RuntimeError(message))
                raise
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                request.sent = False
                message = _not_sent(request, "the session connection already ended")
                self._settle(request, (False, message) if kind == "call" else None,
                             None if kind == "call" else RuntimeError(message))
                return
            except McpError as exc:
                ended = "connection closed" in str(exc).lower() or "connection lost" in str(exc).lower()
                message = (_uncertain(request, self.name, "the connection ended before its answer arrived")
                           if ended else str(exc)[:500])
                self._settle(request, (ended, message) if kind == "call" else None,
                             None if kind == "call" else RuntimeError(message))
                if ended:
                    return
            except Exception as exc:
                from gideon.integrations.mcp_status import safe_text

                message = _uncertain(request, self.name, f"could not read its answer: {safe_text(str(exc))[:300]}")
                self._settle(request, (True, message) if kind == "call" else None,
                             None if kind == "call" else RuntimeError(message))
            else:
                self._settle(request, value)


def _coerce_output(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into the text string the loop feeds back."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        data = getattr(block, "data", None)
        if data is not None:
            parts.append(f"[{getattr(block, 'mimeType', 'binary')} data]")
    if parts:
        return "\n".join(parts)
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        import json

        try:
            return json.dumps(dump(), default=str)
        except Exception:  # noqa: BLE001
            pass
    return str(result)


def _is_poolable(spec: dict[str, Any]) -> bool:
    """Whether a server is safe to SHARE across sessions.

    Safe-by-default means *not* shared: a server is pooled (one connection for
    all sessions) only when it explicitly declares ``poolable: true``. Stateful
    servers (a browser with a logged-in page, a shell with a cwd) must default to
    per-session isolation so one session's state can't leak into another's."""
    return bool(spec.get("poolable", False))


def normalize_transport(spec: dict[str, Any]) -> str:
    """Return the one runtime transport name accepted by discovery and clients."""
    url = spec.get("url") or spec.get("endpoint")
    command = spec.get("command")
    transport = str(spec.get("transport") or "").strip().lower().replace("_", "-")
    if url:
        if command:
            raise ValueError("MCP server cannot define both command and URL")
        if transport in ("", "sse"):
            return "sse"
        if transport in ("http", "streamable-http"):
            return "streamable-http"
        raise ValueError("unsupported MCP remote transport")
    if not command:
        raise ValueError("MCP server needs one command or URL")
    if transport not in ("", "stdio"):
        raise ValueError("unsupported MCP command transport")
    return "stdio"


def _spec_hash(spec: dict[str, Any]) -> str:
    """A stable content hash of the connection-defining fields of a server spec, so two
    servers sharing a NAME but differing in command/args/env/url/transport get DISTINCT
    pool entries instead of colliding on one connection (P23e). Uses sha256 over a
    sort-keyed JSON of only the fields that change what process/endpoint we talk to —
    NEVER Python ``hash()`` (its per-process salt would give a different key every run,
    breaking any cross-process/cross-surface sharing that keys off this)."""
    import hashlib
    import json

    material = {
        "command": spec.get("command", ""),
        "args": spec.get("args", []),
        "env": spec.get("env", {}),
        "url": spec.get("url", ""),
        "transport": spec.get("transport", ""),
        "allowElicitation": spec.get("allowElicitation") is True,
        "oauth": spec.get("oauth", {}),
        "headers": spec.get("headers", {}),
    }
    blob = json.dumps(material, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_ConnKey = tuple[str, str, str]


def _conn_key(name: str, spec: dict[str, Any], session_key: str) -> _ConnKey:
    """Registry key for a (server, caller). Poolable → shared scope ``""``; otherwise
    scoped to the session. The content hash disambiguates same-name/different-spec."""
    scope = "" if _is_poolable(spec) else (session_key or "")
    return (name, scope, _spec_hash(spec))


class McpClientRegistry:
    """Process-wide registry of live MCP server connections (Gideon scope).

    Connections are keyed by ``(server_name, scope)``. Poolable servers share one
    connection (scope ``""``) across every session — the optimal, already-pooled
    path. Non-poolable (stateful) servers get one connection PER session so their
    state can't leak across sessions (safe-by-default isolation). Idle connections
    are reaped on a sweep so resident memory tracks *active* (server, session)
    pairs, not every session that ever touched a server."""

    def __init__(self, elicitation_handler: ElicitationHandler | None = None) -> None:
        self._conns: dict[_ConnKey, McpServerConn] = {}
        self._specs: dict[str, dict[str, Any]] = {}
        self._sweeper: asyncio.Task | None = None
        self._stats = {"spawns": 0, "reaps": 0, "served": 0, "evicted": 0}
        self._elicitation_handler = elicitation_handler

    def _canonical_key(self, name: str) -> _ConnKey | None:
        """The shared (scope ``""``) key for a configured server, or None if unknown.
        Computed via the current spec so it tracks a content-hash change on reconcile.
        """
        spec = self._specs.get(name)
        return _conn_key(name, spec, "") if spec is not None else None

    def items(self):
        """Canonical (scope ``""``) connection per configured server — the Tools
        page lists each server once. The tool *surface* is identical across scopes,
        so listing from the canonical connection is correct; per-session isolation
        only matters for stateful ``call_tool`` traffic."""
        out = []
        for name in self._specs:
            key = self._canonical_key(name)
            if key is not None and key in self._conns:
                out.append((name, self._conns[key]))
        return out

    def get(self, name: str, session_key: str = "") -> McpServerConn | None:
        """Resolve the connection a caller should use for ``name``.

        - Poolable server → the shared canonical connection (one for all sessions).
        - Stateful server + a session key → a per-session connection, created on
          demand, so that session's state can't leak into another's.
        - Stateful server + no session key → the canonical connection (listing /
          no session to isolate).

        Returns ``None`` for an unknown server."""
        spec = self._specs.get(name)
        if spec is None:
            return None
        key = _conn_key(name, spec, session_key)
        conn = self._conns.get(key)
        if conn is None:
            conn = McpServerConn(
                name, spec, scope=key[1], elicitation_handler=self._elicitation_handler
            )
            self._conns[key] = conn
            self._stats["spawns"] += 1
        self._stats["served"] += 1
        return conn

    def load_from_specs(self, specs: dict[str, dict[str, Any]]) -> None:
        """Reconcile the registry to ``specs`` ({name: spec}). Each configured
        server gets its canonical (scope ``""``) connection eagerly (lazy-connected
        on first use); per-session connections for stateful servers are added on
        demand by :meth:`get`. Removed servers — AND servers whose spec content
        changed (new content hash) — have their stale connections dropped."""
        self._specs = {
            n: s
            for n, s in specs.items()
            if isinstance(s, dict) and not s.get("disabled")
        }
        want_canonical = {self._canonical_key(n) for n in self._specs}
        for name, spec in self._specs.items():
            key = _conn_key(name, spec, "")
            if key not in self._conns:
                self._conns[key] = McpServerConn(
                    name,
                    spec,
                    scope="",
                    elicitation_handler=self._elicitation_handler,
                )
                self._stats["spawns"] += 1
        for key in list(self._conns):
            name, scope, _hash = key
            gone = name not in self._specs
            stale_hash = key[2] != _spec_hash(self._specs[name]) if not gone else False
            if gone or stale_hash:
                from gideon.integrations.mcp_stdio import stop_finishing_soon

                stop_finishing_soon(lambda server, removed=name: server == removed)
                conn = self._conns.pop(key)
                asyncio.ensure_future(conn.shutdown())

    def evict_session(self, session_key: str) -> None:
        """Shut down + drop all connections scoped to an ending session. Shared
        (poolable, scope ``""``) connections are untouched."""
        if not session_key:
            return
        for key in [k for k in self._conns if k[1] == session_key]:
            conn = self._conns.pop(key)
            self._stats["evicted"] += 1
            asyncio.ensure_future(conn.shutdown())

    def invalidate_server(self, name: str, *, serving_only: bool = False) -> int:
        """Drop every live scope for one server after its authority changes."""
        from gideon.integrations.mcp_stdio import stop_finishing_soon

        if not serving_only:
            stop_finishing_soon(lambda server: server == name)
        keys = [key for key, conn in self._conns.items() if key[0] == name and (not serving_only or conn._ready.is_set())]
        for key in keys:
            conn = self._conns.pop(key)
            asyncio.ensure_future(conn.shutdown())
        return len(keys)

    def sweep_idle(self, ttl_secs: float = _IDLE_TTL_SECS) -> int:
        """Reap connections unused for longer than ``ttl_secs``. Returns the count
        reaped. A reaped server simply re-lazy-starts on its next use."""
        now = time.monotonic()
        reaped = 0
        for key in [k for k, c in self._conns.items() if now - c.last_used > ttl_secs]:
            conn = self._conns.pop(key)
            asyncio.ensure_future(conn.shutdown())
            reaped += 1
        self._stats["reaps"] += reaped
        if reaped:
            logger.debug("MCP idle sweep reaped %d connection(s)", reaped)
        return reaped

    def start_sweeper(self) -> None:
        """Start the periodic idle-eviction loop (idempotent)."""
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(
                self._sweep_loop(), name="mcp-idle-sweeper"
            )

    async def _sweep_loop(self) -> None:
        from gideon import shutdown_event

        while not shutdown_event.is_set():
            try:
                await asyncio.sleep(_SWEEP_INTERVAL_SECS)
                self.sweep_idle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("MCP idle sweep failed", exc_info=True)

    def pool_stats(self) -> dict[str, Any]:
        """P23d observability: process-lifetime counters + a live snapshot of the pool.

        ``shared_conns`` counts poolable (scope ``""``) connections — the ones serving
        many callers from ONE process; ``session_conns`` counts per-session (isolated)
        ones. ``dedup_saved`` estimates connections avoided by pooling: every ``served``
        beyond the number of live connections is a call that reused an existing conn
        instead of spawning. Pure/read-only — safe to call from an API handler."""
        live = len(self._conns)
        shared = sum(1 for k in self._conns if k[1] == "")
        served = self._stats["served"]
        return {
            "live_connections": live,
            "shared_conns": shared,
            "session_conns": live - shared,
            "configured_servers": len(self._specs),
            "spawns": self._stats["spawns"],
            "reaps": self._stats["reaps"],
            "served": served,
            "evicted": self._stats["evicted"],
            "reused": max(0, served - self._stats["spawns"]),
        }

    async def shutdown_all(self) -> None:
        from gideon.integrations.mcp_stdio import stop_finishing

        await stop_finishing(lambda _name: True)
        if self._sweeper and not self._sweeper.done():
            self._sweeper.cancel()
            self._sweeper = None
        await asyncio.gather(
            *(c.shutdown() for c in self._conns.values()), return_exceptions=True
        )
        self._conns.clear()
        await stop_finishing(lambda _name: True)


_registry: McpClientRegistry | None = None
_elicitation_handler: ElicitationHandler | None = None


def set_mcp_elicitation_handler(handler: ElicitationHandler | None) -> None:
    global _elicitation_handler
    _elicitation_handler = handler
    if _registry is not None:
        _registry._elicitation_handler = handler
        for conn in _registry._conns.values():
            conn._elicitation_handler = handler


def _gideon_mcp_specs() -> dict[str, dict[str, Any]]:
    """Load the Gideon-scope server specs from ``~/.gideon/mcp.json``.

    This is the single store the native client spawns from — the one the MCP
    Tools provider card writes and ``/api/mcp/apply`` imports into.
    """
    from gideon.extensions.providers.mcp_instances import _load

    data = _load()
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return {}
    from gideon.security.mcp_grants import allowed

    admitted: dict[str, dict[str, Any]] = {}
    for name, spec in servers.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        candidate = {**spec, "name": name, "source": "mcp.json"}
        if allowed(candidate):
            admitted[name] = {**spec, "source": "mcp.json"}
    return admitted


def get_mcp_client_registry() -> McpClientRegistry | None:
    """Return the process-wide registry, or ``None`` if the SDK is absent.

    Lazily loads specs from ``~/.gideon/mcp.json`` on first call. Returns
    ``None`` (not an empty registry) when the ``mcp`` extra isn't installed, so
    callers can distinguish "no SDK" from "SDK present, no servers".
    """
    if not mcp_sdk_available():
        return None
    global _registry
    if _registry is None:
        _registry = McpClientRegistry(elicitation_handler=_elicitation_handler)
    _registry.load_from_specs(_gideon_mcp_specs())
    return _registry


def with_mcp_session_eviction(
    prior: "Callable[[str], Awaitable[object]] | None",
) -> "Callable[[str], Awaitable[None]]":
    """Wrap a session-expire callback so it also evicts that session's per-session
    MCP connections (stateful servers). Composed onto the existing expire chain so
    it runs ALONGSIDE consolidation + workflow cleanup, never instead of them.
    Best-effort: a failure here never blocks the rest of session teardown."""

    async def _expire(session_key: str) -> None:
        if prior is not None:
            try:
                await prior(session_key)
            except Exception:
                logger.warning(
                    "session-expire prior callback failed for %s",
                    session_key,
                    exc_info=True,
                )
        try:
            if _registry is not None:
                _registry.evict_session(session_key)
        except Exception:
            logger.debug(
                "MCP session eviction failed for %s", session_key, exc_info=True
            )
        from gideon.integrations.mcp_hypermid import evict_hypermid_session

        await evict_hypermid_session(session_key)

    return _expire
