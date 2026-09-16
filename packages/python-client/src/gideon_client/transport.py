"""Connection ownership, credentials and retry scheduling for Gideon clients."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp

from .errors import ErrorCode, GideonError, http_error

_DEFAULT_PORT = 10000
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BASE_DELAY = 1.0
_MAX_BACKOFF = 30.0
_LOOPBACK_HOSTS = frozenset(("localhost", "127.0.0.1", "::1", "[::1]"))


def _is_loopback(url: str) -> bool:
    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        return False
    return hostname in _LOOPBACK_HOSTS


def _compute_backoff(attempt: int, base_delay: float) -> float:
    return min(_MAX_BACKOFF, base_delay * (2 ** attempt))


def app_home() -> Path:
    configured = os.environ.get("GIDEON_HOME")
    return Path(configured) if configured is not None else Path.home() / ".gideon"


def _read_app_secret(app_name: str) -> str:
    secret_file = app_home().joinpath("apps", app_name, ".app_secret")
    try:
        return secret_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


async def _exchange_app_token(base_url: str, app_name: str, secret: str) -> str:
    endpoint = f"{base_url}/api/apps/{app_name}/token"
    headers = {"X-App-Secret": secret, "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as connection:
        async with connection.post(endpoint, headers=headers) as response:
            if response.ok:
                payload = await response.json()
                return str(payload.get("token", ""))
            raise GideonError(
                ErrorCode.AUTH_EXPIRED,
                f"App token exchange failed: HTTP {response.status}",
                status=response.status,
            )


class GatewayTransport:
    """A reusable HTTP connection with bounded retries and explicit auth renewal."""

    def __init__(
        self, *, base_url: str = "", token: str = "", app_name: str = "",
        timeout: int = _DEFAULT_TIMEOUT, max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY,
        on_auth_expired: Callable[[], Awaitable[str]] | None = None,
    ):
        address = base_url or "http://localhost:" + os.environ.get("GIDEON_PORT", str(_DEFAULT_PORT))
        self.base_url = address.rstrip("/")
        self.token, self.app_name = token, app_name
        self.timeout, self.max_retries = timeout, max_retries
        self.retry_base_delay = retry_base_delay
        self._session: aiohttp.ClientSession | None = None
        self._app_secret = _read_app_secret(app_name) if app_name and not (token or on_auth_expired) else ""
        self._on_auth_expired = on_auth_expired or (self._auto_refresh_token if self._app_secret else None)

    async def _auto_refresh_token(self) -> str:
        return await _exchange_app_token(self.base_url, self.app_name, self._app_secret)

    async def authenticate(self) -> bool:
        if not (self.token or not self._app_secret or not self.app_name):
            try:
                self.token = await self._auto_refresh_token()
            except Exception:
                return False
        return True

    async def __aenter__(self) -> GatewayTransport:
        self._ensure_session()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        connection, self._session = self._session, None
        if connection is not None:
            await connection.close()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        result = {"Content-Type": "application/json"}
        if self.token:
            result["Cookie"] = "gideon_token=" + self.token
        return result

    def _check_auth(self) -> None:
        if self.token or _is_loopback(self.base_url):
            return
        raise GideonError(ErrorCode.AUTH_REQUIRED, "Auth token required for remote Gateway connections")

    def _retry_delay(self, attempt: int, response: aiohttp.ClientResponse | None) -> float:
        if response is not None and response.status == 429:
            try:
                return float(response.headers["Retry-After"])
            except (KeyError, ValueError, TypeError):
                pass
        return _compute_backoff(attempt, self.retry_base_delay)

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        self._check_auth()
        connection = self._ensure_session()
        failure: GideonError | None = None
        for attempt in range(self.max_retries + 1):
            retry_delay = self._retry_delay(attempt, None)
            request_options: dict[str, Any] = {"headers": self._auth_headers()}
            if body is not None:
                request_options["json"] = body
            try:
                async with connection.request(method, self.base_url + path, **request_options) as response:
                    if response.status in (401, 403) and attempt == 0 and self._on_auth_expired:
                        try:
                            renewed = await self._on_auth_expired()
                        except Exception:
                            pass
                        else:
                            self.token = renewed
                            continue
                    if response.ok:
                        if "application/json" in response.headers.get("content-type", ""):
                            return await response.json()
                        return {}
                    failure = http_error(response.status, await response.text() or None)
                    if response.status != 429 and 400 <= response.status < 500:
                        raise failure
                    retry_delay = self._retry_delay(attempt, response)
            except GideonError:
                raise
            except Exception as error:
                failure = GideonError(ErrorCode.NETWORK_ERROR, str(error))
            if attempt < self.max_retries:
                await asyncio.sleep(retry_delay)
        raise failure or GideonError(ErrorCode.NETWORK_ERROR, "Request failed")

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def _post(self, path: str, body: Any = None) -> Any:
        return await self._request("POST", path, body)

    async def _put(self, path: str, body: Any = None) -> Any:
        return await self._request("PUT", path, body)

    async def _delete(self, path: str) -> Any:
        return await self._request("DELETE", path)
