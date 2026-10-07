"""Operator-managed MCP OAuth with private per-server token files."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
import webbrowser
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from gideon.core.config.loader import config_dir

_CALLBACK_STATE_TTL = 600.0
_CALLBACK_STATE_KEY = secrets.token_bytes(32)
_PENDING_CALLBACKS: dict[str, "PendingCallback"] = {}
_CALLBACK_FUTURES: dict[str, asyncio.Future] = {}
_AUTHORIZATION_TASKS: dict[str, asyncio.Task] = {}


@dataclass(frozen=True, slots=True)
class PendingCallback:
    """One in-process OAuth callback bound to its authenticated owner and server."""

    principal: str
    tenant: str
    server: str
    resource: str
    revision: str
    sdk_state: str
    created_at: float
    issuer: str = ""


class OAuthCallbackError(ValueError):
    """A dashboard OAuth callback is expired, replayed, or bound elsewhere."""


def _state_signature(nonce: str) -> str:
    digest = hmac.new(
        _CALLBACK_STATE_KEY, nonce.encode("ascii"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _prune_pending_callbacks(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    expired = [
        nonce
        for nonce, pending in _PENDING_CALLBACKS.items()
        if current - pending.created_at > _CALLBACK_STATE_TTL
    ]
    for nonce in expired:
        _PENDING_CALLBACKS.pop(nonce, None)
        callback = _CALLBACK_FUTURES.pop(nonce, None)
        task = _AUTHORIZATION_TASKS.pop(nonce, None)
        if callback is not None and not callback.done():
            callback.cancel()
        if task is not None and not task.done():
            task.cancel()


def issue_callback_state(
    principal: Any,
    *,
    server: str,
    resource: str,
    revision: str,
    sdk_state: str,
    issuer: str = "",
) -> str:
    """Create an opaque signed, one-time state tied to the exact OAuth attempt."""
    from gideon.security.approval_answer import OWNER, Principal

    if (
        not isinstance(principal, Principal)
        or principal.kind != OWNER
        or not principal.name
    ):
        raise PermissionError("MCP OAuth requires an authenticated owner")
    if not all(
        isinstance(value, str) and value
        for value in (server, resource, revision, sdk_state)
    ):
        raise ValueError("MCP OAuth callback binding is incomplete")
    if issuer and not issuer.startswith("https://"):
        raise ValueError("MCP OAuth issuer must be an HTTPS URL")
    parsed = urlsplit(resource)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("MCP OAuth resource must be a credential-free HTTPS URL")

    now = time.monotonic()
    _prune_pending_callbacks(now)
    nonce = secrets.token_urlsafe(32)
    _PENDING_CALLBACKS[nonce] = PendingCallback(
        principal=principal.name,
        tenant=principal.tenant,
        server=server,
        resource=resource,
        revision=revision,
        sdk_state=sdk_state,
        created_at=now,
        issuer=issuer,
    )
    return nonce + "." + _state_signature(nonce)


def consume_callback_state(
    state: str,
    principal: Any,
    *,
    server: str,
    resource: str,
    revision: str,
) -> PendingCallback:
    """Validate and consume a callback state exactly once for its initiating owner."""
    from gideon.security.approval_answer import OWNER, Principal

    if (
        not isinstance(principal, Principal)
        or principal.kind != OWNER
        or not principal.name
    ):
        raise PermissionError("MCP OAuth callback requires an authenticated owner")
    if not isinstance(state, str) or state.count(".") != 1:
        raise OAuthCallbackError("MCP OAuth callback state is invalid")
    nonce, signature = state.split(".", 1)
    expected = _state_signature(nonce)
    if not hmac.compare_digest(signature, expected):
        raise OAuthCallbackError("MCP OAuth callback state is invalid")
    now = time.monotonic()
    _prune_pending_callbacks(now)
    pending = _PENDING_CALLBACKS.get(nonce)
    if pending is None:
        raise OAuthCallbackError("MCP OAuth callback state is expired or already used")
    if (
        pending.principal != principal.name
        or pending.tenant != principal.tenant
        or pending.server != server
        or pending.resource != resource
        or pending.revision != revision
    ):
        raise OAuthCallbackError(
            "MCP OAuth callback does not match the initiating owner or server"
        )
    del _PENDING_CALLBACKS[nonce]
    return pending


def callback_binding(state: str, principal: Any) -> PendingCallback:
    """Read a signed callback binding without consuming its one-time state."""
    from gideon.security.approval_answer import OWNER, Principal

    if (
        not isinstance(principal, Principal)
        or principal.kind != OWNER
        or not principal.name
    ):
        raise PermissionError("MCP OAuth callback requires an authenticated owner")
    if not isinstance(state, str) or state.count(".") != 1:
        raise OAuthCallbackError("MCP OAuth callback state is invalid")
    nonce, signature = state.split(".", 1)
    if not hmac.compare_digest(signature, _state_signature(nonce)):
        raise OAuthCallbackError("MCP OAuth callback state is invalid")
    _prune_pending_callbacks()
    pending = _PENDING_CALLBACKS.get(nonce)
    if pending is None:
        raise OAuthCallbackError("MCP OAuth callback state is expired or already used")
    if pending.principal != principal.name or pending.tenant != principal.tenant:
        raise OAuthCallbackError("MCP OAuth callback belongs to another owner")
    return pending


def _canonical_resource(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise OAuthCallbackError("MCP OAuth resource must be a canonical HTTPS URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise OAuthCallbackError("MCP OAuth resource has an invalid port") from exc
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and port != 443:
        host = f"{host}:{port}"
    return urlunsplit(("https", host, parsed.path or "/", "", ""))


async def begin_dashboard_authorization(
    name: str,
    server: dict[str, Any],
    principal: Any,
    *,
    revision: str,
    redirect_uri: str,
) -> dict[str, str]:
    """Start the SDK's real MCP OAuth challenge flow and return its safe login URL."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from gideon.security import mcp_grants
    from gideon.security.approval_answer import OWNER, Principal

    if (
        not isinstance(principal, Principal)
        or principal.kind != OWNER
        or not principal.name
    ):
        raise PermissionError("MCP OAuth requires an authenticated owner")
    if (
        not isinstance(server, dict)
        or server.get("name") != name
        or server.get("source") != "mcp.json"
    ):
        raise ValueError(
            "MCP OAuth is available only for the current owner-managed server"
        )
    url = str(server.get("url") or server.get("endpoint") or "")
    if urlsplit(url).scheme.lower() != "https":
        raise ValueError("MCP OAuth requires an HTTPS remote server")
    if mcp_grants.revision(server) != revision or not mcp_grants.allowed(server):
        raise ValueError("Allow the current MCP server definition before signing in")

    authorization_url: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    callback: asyncio.Future[tuple[str, str]] = (
        asyncio.get_running_loop().create_future()
    )
    flow_state: dict[str, str] = {}
    provider: dict[str, Any] = {}

    async def redirect(auth_url: str) -> None:
        parsed = urlsplit(auth_url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise OAuthCallbackError("MCP authorization server returned an unsafe URL")
        query = parse_qs(parsed.query, keep_blank_values=True)
        states = query.get("state", [])
        if len(states) != 1 or not states[0]:
            raise OAuthCallbackError("MCP authorization request has no unique state")
        resources = query.get("resource", [])
        if len(resources) > 1:
            raise OAuthCallbackError(
                "MCP authorization request has an ambiguous resource"
            )
        selected_resource = resources[0] if resources else ""
        if not selected_resource and provider.get("auth") is not None:
            selected_resource = str(provider["auth"].context.get_resource_url())
        resource = _canonical_resource(selected_resource or url)
        sdk_state = states[0]
        current_auth = provider.get("auth")
        auth_context = current_auth.context if current_auth is not None else None
        oauth_metadata = getattr(auth_context, "oauth_metadata", None)
        issuer = str(
            getattr(oauth_metadata, "issuer", "")
            or getattr(auth_context, "auth_server_url", "")
            or ""
        )
        signed_state = issue_callback_state(
            principal,
            server=name,
            resource=resource,
            revision=revision,
            sdk_state=sdk_state,
            issuer=issuer,
        )
        nonce = signed_state.split(".", 1)[0]
        flow_state["nonce"] = nonce
        _CALLBACK_FUTURES[nonce] = callback
        query["state"] = [signed_state]
        safe_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query, doseq=True),
                "",
            )
        )
        if not authorization_url.done():
            authorization_url.set_result(safe_url)

    async def receive_callback() -> tuple[str, str]:
        return await callback

    async def authorize() -> None:
        try:
            from gideon.integrations.mcp_client import (
                normalize_transport,
                remote_http_factory,
            )

            auth = oauth_provider(
                name,
                url,
                server,
                redirect_handler=redirect,
                callback_handler=receive_callback,
                redirect_uri=redirect_uri,
            )
            provider["auth"] = auth
            factory = remote_http_factory(url, getattr(auth, "context", None))
            if normalize_transport(server) == "sse":
                from mcp.client.sse import sse_client

                async with sse_client(url, auth=auth, httpx_client_factory=factory) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
            else:
                async with streamablehttp_client(
                    url,
                    auth=auth,
                    httpx_client_factory=factory,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
        except Exception:
            if not authorization_url.done():
                authorization_url.set_exception(
                    ValueError("MCP OAuth authorization could not start")
                )
            raise

    task = asyncio.create_task(authorize())
    try:
        auth_url = await asyncio.wait_for(authorization_url, timeout=30)
    except Exception:
        task.cancel()
        raise
    nonce = flow_state.get("nonce")
    if not nonce:
        task.cancel()
        raise OAuthCallbackError("MCP authorization state was not created")
    _AUTHORIZATION_TASKS[nonce] = task
    return {"authorization_url": auth_url, "state": "signin", "server": name}


async def complete_dashboard_callback(
    state: str,
    code: str,
    principal: Any,
    *,
    server: str,
    resource: str,
    revision: str,
    issuer: str = "",
) -> dict[str, str]:
    """Consume an owner-bound callback, let the SDK exchange it, and return safe status."""
    if (
        not isinstance(code, str)
        or not code
        or len(code) > 8192
        or any(ord(ch) < 32 for ch in code)
    ):
        raise OAuthCallbackError("MCP OAuth callback has no valid authorization code")
    pending = consume_callback_state(
        state,
        principal,
        server=server,
        resource=resource,
        revision=revision,
    )
    if issuer and issuer != pending.issuer:
        nonce = state.split(".", 1)[0]
        callback = _CALLBACK_FUTURES.pop(nonce, None)
        task = _AUTHORIZATION_TASKS.pop(nonce, None)
        if callback is not None and not callback.done():
            callback.cancel()
        if task is not None and not task.done():
            task.cancel()
        raise OAuthCallbackError(
            "MCP OAuth callback issuer does not match the authorization server"
        )
    nonce = state.split(".", 1)[0]
    callback = _CALLBACK_FUTURES.pop(nonce, None)
    task = _AUTHORIZATION_TASKS.get(nonce)
    if callback is None or task is None or callback.done():
        _AUTHORIZATION_TASKS.pop(nonce, None)
        raise OAuthCallbackError("MCP OAuth authorization expired")
    callback.set_result((code, pending.sdk_state))
    try:
        await asyncio.wait_for(task, timeout=60)
    except Exception as exc:
        raise OAuthCallbackError("MCP OAuth authorization failed") from exc
    finally:
        _AUTHORIZATION_TASKS.pop(nonce, None)
    return {"state": "connected", "server": server}


def cancel_dashboard_callback(state: str, principal: Any) -> None:
    """Consume a declined authorization response and stop its pending SDK attempt."""
    binding = callback_binding(state, principal)
    nonce = state.split(".", 1)[0]
    consume_callback_state(
        state,
        principal,
        server=binding.server,
        resource=binding.resource,
        revision=binding.revision,
    )
    callback = _CALLBACK_FUTURES.pop(nonce, None)
    task = _AUTHORIZATION_TASKS.pop(nonce, None)
    if callback is not None and not callback.done():
        callback.cancel()
    if task is not None and not task.done():
        task.cancel()


def _owner(name: str):
    from gideon.core.config.secret_refs import SecretOwner

    return SecretOwner("MCP_OAUTH", name)


class McpOAuthStorage:
    def __init__(self, name: str, url: str) -> None:
        self.name = name
        digest = hashlib.sha256(f"{name}\0{url}".encode()).hexdigest()
        self.path = config_dir() / "mcp-oauth" / f"{digest}.json"

    def _private_directory(self) -> None:
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise PermissionError("MCP OAuth token directory is not private")
        if metadata.st_mode & 0o077:
            directory.chmod(0o700)

    def _read_references(self) -> dict[str, Any]:
        try:
            self._private_directory()
            metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise PermissionError("MCP OAuth token file is not private")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

        # Migrate the old token/client documents once; callers only ever receive
        # the resolved payload, while this portable file retains references.
        from gideon.core.config.secret_refs import purge_unused, store

        values: dict[str, str] = {}
        previous: dict[str, str] = {}
        for field in ("tokens", "client_info"):
            value = data.get(field)
            if isinstance(value, dict):
                values[field] = json.dumps(value, separators=(",", ":"))
            elif isinstance(value, str):
                previous[field] = value
                values[field] = value
        if not values:
            return data
        references = store(
            values, owner=_owner(self.name), declared=set(values), previous=previous
        )
        if references != previous:
            try:
                self._write_references(references)
            except Exception:
                purge_unused(_owner(self.name), previous)
                raise
            purge_unused(_owner(self.name), references)
        return references

    def _read(self) -> dict[str, Any]:
        from gideon.core.config.secret_refs import resolve

        refs = self._read_references()
        resolved = resolve(refs, owner=_owner(self.name))
        result = {}
        for field in ("tokens", "client_info"):
            value = resolved.get(field)
            if isinstance(value, str):
                try:
                    result[field] = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "MCP OAuth credential record is malformed"
                    ) from exc
        saved_at = refs.get("tokens_saved_at")
        if isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool):
            result["tokens_saved_at"] = float(saved_at)
        return result

    def _write_references(self, data: dict[str, Any]) -> None:
        self._private_directory()
        if self.path.exists() or self.path.is_symlink():
            metadata = self.path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise PermissionError(
                    "MCP OAuth token path is not a private regular file"
                )
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write(self, data: dict[str, Any]) -> None:
        from gideon.core.config.secret_refs import purge_unused, store

        old = self._read_references()
        values = {
            field: json.dumps(data[field], separators=(",", ":"))
            for field in ("tokens", "client_info")
            if isinstance(data.get(field), dict)
        }
        refs = store(
            values, owner=_owner(self.name), declared=set(values), previous=old
        )
        if isinstance(data.get("tokens_saved_at"), (int, float)):
            refs["tokens_saved_at"] = float(data["tokens_saved_at"])
        try:
            self._write_references(refs)
        except Exception:
            purge_unused(_owner(self.name), old)
            raise
        purge_unused(_owner(self.name), refs)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if isinstance(data, dict) else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        data["tokens_saved_at"] = time.time()
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        data = self._read().get("client_info")
        return (
            OAuthClientInformationFull.model_validate(data)
            if isinstance(data, dict)
            else None
        )

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)

    async def status(self) -> dict[str, Any]:
        data = self._read()
        tokens = data.get("tokens")
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            return {"state": "signin"}
        saved_at = data.get("tokens_saved_at")
        expires_in = tokens.get("expires_in")
        renewal_needed = (
            isinstance(saved_at, (int, float))
            and isinstance(expires_in, (int, float))
            and float(saved_at) + float(expires_in) <= time.time() + 300
        )
        return {
            "state": "renewal_needed" if renewal_needed else "connected",
            "renewable": bool(tokens.get("refresh_token")),
        }

    async def has_tokens(self) -> bool:
        tokens = self._read().get("tokens")
        return isinstance(tokens, dict) and bool(tokens.get("access_token"))


def purge_server(name: str, url: str) -> None:
    """Remove this server's legacy private OAuth cache after owner deletion."""
    _prune_pending_callbacks()
    for nonce, pending in list(_PENDING_CALLBACKS.items()):
        if pending.server != name:
            continue
        _PENDING_CALLBACKS.pop(nonce, None)
        callback = _CALLBACK_FUTURES.pop(nonce, None)
        task = _AUTHORIZATION_TASKS.pop(nonce, None)
        if callback is not None and not callback.done():
            callback.cancel()
        if task is not None and not task.done():
            task.cancel()
    digest = hashlib.sha256(f"{name}\0{url}".encode()).hexdigest()
    path = config_dir() / "mcp-oauth" / f"{digest}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    from gideon.core.config.secret_refs import purge

    purge([_owner(name).prefix])


def oauth_provider(
    name: str,
    url: str,
    spec: dict[str, Any],
    *,
    redirect_handler=None,
    callback_handler=None,
    redirect_uri: str | None = None,
):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    settings = spec.get("oauth") or {}
    if not isinstance(settings, dict):
        raise ValueError("MCP OAuth settings must be an object")
    redirect_uri = str(
        redirect_uri or settings.get("redirect_uri") or "http://127.0.0.1:8765/callback"
    )
    parsed = urlsplit(redirect_uri)
    if parsed.scheme == "https":
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/api/mcp/oauth/callback"
        ):
            raise ValueError(
                "MCP OAuth dashboard redirect_uri must be an HTTPS callback URL"
            )
    elif (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.port
        or parsed.path not in {"/callback", "/api/mcp/oauth/callback"}
    ):
        raise ValueError(
            "MCP OAuth redirect_uri must use the dashboard HTTPS callback or local loopback callback"
        )
    from pydantic import AnyUrl

    metadata = OAuthClientMetadata(
        client_name="Gideon",
        redirect_uris=[AnyUrl(redirect_uri)],
        scope=str(settings.get("scope") or "") or None,
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    return OAuthClientProvider(
        server_url=url,
        client_metadata=metadata,
        storage=McpOAuthStorage(name, url),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


async def authorize_server(name: str, *, manual: bool = False) -> dict[str, Any]:
    """Complete authorization through a local callback, then prove MCP initialize."""
    from aiohttp import web
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from gideon.integrations.mcp_client import _gideon_mcp_specs

    spec = _gideon_mcp_specs().get(name)
    if not isinstance(spec, dict) or not isinstance(spec.get("oauth"), dict):
        raise ValueError(f"MCP server {name!r} is not configured for OAuth")
    url = str(spec.get("url") or spec.get("endpoint") or "")
    if not url.startswith("https://"):
        raise ValueError("MCP OAuth requires an HTTPS server URL")
    redirect_uri = str(
        spec["oauth"].get("redirect_uri") or "http://127.0.0.1:8765/callback"
    )
    parsed = urlsplit(redirect_uri)
    if parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path != "/callback":
        raise ValueError("invalid loopback redirect_uri")
    callback = asyncio.get_running_loop().create_future()

    async def receive(request: web.Request) -> web.Response:
        if not callback.done():
            callback.set_result(
                (request.query.get("code", ""), request.query.get("state"))
            )
        return web.Response(
            text="Gideon MCP authorization received. You may close this tab."
        )

    app = web.Application()
    app.router.add_get("/callback", receive)
    runner = None
    if not manual:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", parsed.port)
        await site.start()
    try:

        async def redirect(auth_url: str) -> None:
            print(f"Open this MCP authorization URL: {auth_url}", flush=True)
            if not manual:
                webbrowser.open(auth_url)

        async def await_callback() -> tuple[str, str | None]:
            if manual:
                pasted = await asyncio.to_thread(
                    input, "Paste the final callback URL: "
                )
                result = urlsplit(pasted.strip())
                if (
                    result.scheme != parsed.scheme
                    or result.netloc != parsed.netloc
                    or result.path != parsed.path
                ):
                    raise ValueError(
                        "callback URL did not match the configured redirect_uri"
                    )
                query = parse_qs(result.query)
                code = (query.get("code") or [""])[0]
                state_values = query.get("state")
                state = state_values[0] if state_values else None
                if not code:
                    raise ValueError("callback URL has no authorization code")
                return code, state
            return await asyncio.wait_for(callback, timeout=300)

        auth = oauth_provider(
            name, url, spec, redirect_handler=redirect, callback_handler=await_callback
        )
        async with streamablehttp_client(url, auth=auth) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
        return {"server": name, "authorized": True, "tool_count": len(tools.tools)}
    finally:
        if runner is not None:
            await runner.cleanup()
