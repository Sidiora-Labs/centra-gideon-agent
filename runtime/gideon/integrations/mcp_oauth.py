"""Operator-managed MCP OAuth with private per-server token files."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import stat
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gideon.core.config.loader import config_dir


class McpOAuthStorage:
    def __init__(self, name: str, url: str) -> None:
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

    def _read(self) -> dict[str, Any]:
        try:
            self._private_directory()
            metadata = self.path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise PermissionError("MCP OAuth token file is not private")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self._private_directory()
        if self.path.exists() or self.path.is_symlink():
            metadata = self.path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise PermissionError("MCP OAuth token path is not a private regular file")
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

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if isinstance(data, dict) else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        data = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if isinstance(data, dict) else None

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


def oauth_provider(name: str, url: str, spec: dict[str, Any], *, redirect_handler=None, callback_handler=None):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    settings = spec.get("oauth") or {}
    if not isinstance(settings, dict):
        raise ValueError("MCP OAuth settings must be an object")
    redirect_uri = str(settings.get("redirect_uri") or "http://127.0.0.1:8765/callback")
    parsed = urlsplit(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path != "/callback":
        raise ValueError("MCP OAuth redirect_uri must be an http://127.0.0.1:<port>/callback URL")
    metadata = OAuthClientMetadata(
        client_name="Gideon",
        redirect_uris=[redirect_uri],
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
    redirect_uri = str(spec["oauth"].get("redirect_uri") or "http://127.0.0.1:8765/callback")
    parsed = urlsplit(redirect_uri)
    if parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path != "/callback":
        raise ValueError("invalid loopback redirect_uri")
    callback = asyncio.get_running_loop().create_future()

    async def receive(request: web.Request) -> web.Response:
        if not callback.done():
            callback.set_result((request.query.get("code", ""), request.query.get("state")))
        return web.Response(text="Gideon MCP authorization received. You may close this tab.")

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
                pasted = await asyncio.to_thread(input, "Paste the final callback URL: ")
                result = urlsplit(pasted.strip())
                if result.scheme != parsed.scheme or result.netloc != parsed.netloc or result.path != parsed.path:
                    raise ValueError("callback URL did not match the configured redirect_uri")
                query = parse_qs(result.query)
                code = (query.get("code") or [""])[0]
                state = (query.get("state") or [None])[0]
                if not code:
                    raise ValueError("callback URL has no authorization code")
                return code, state
            return await asyncio.wait_for(callback, timeout=300)

        auth = oauth_provider(name, url, spec, redirect_handler=redirect, callback_handler=await_callback)
        async with streamablehttp_client(url, auth=auth) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
        return {"server": name, "authorized": True, "tool_count": len(tools.tools)}
    finally:
        if runner is not None:
            await runner.cleanup()
