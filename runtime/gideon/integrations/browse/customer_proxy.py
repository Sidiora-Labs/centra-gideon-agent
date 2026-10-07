"""Per-session proxy for the owned Chromium process."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import logging
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from gideon.security.net.guard import evaluate
from gideon.security.net.policy import BROWSE, egress_policy_for
from gideon.security.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)
_HEADER_LIMIT = 65536


class CustomerBrowserProxy:
    def __init__(self) -> None:
        self._username = secrets.token_urlsafe(18)
        self._password = secrets.token_urlsafe(32)
        self._authorization = "Basic " + base64.b64encode(
            f"{self._username}:{self._password}".encode("ascii")
        ).decode("ascii")
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("Browser proxy is not running")
        return self._server.sockets[0].getsockname()[1]

    @property
    def credentials(self) -> tuple[str, str]:
        return self._username, self._password

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in tuple(self._clients):
            writer.close()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        self._clients.add(writer)
        try:
            await self._handle(reader, writer)
        except (
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("customer browser proxy failed closed")
        finally:
            self._tasks.discard(task)
            self._clients.discard(writer)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        if len(raw) > _HEADER_LIMIT:
            return
        lines = raw[:-4].split(b"\r\n")
        first = lines[0].decode("ascii")
        method, target, version = first.split(" ")
        if version != "HTTP/1.1" or not method.isalpha():
            return
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            name, sep, value = line.partition(b":")
            if not sep or not name or b"\x00" in value:
                return
            headers.append(
                (name.decode("ascii").lower(), value.strip().decode("latin1"))
            )
        header = dict(headers)
        if len(headers) != len(header):
            return
        authorization = header.get("proxy-authorization", "")
        if not hmac.compare_digest(authorization, self._authorization):
            writer.write(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b'Proxy-Authenticate: Basic realm="Gideon browser"\r\n'
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            return
        connect = method == "CONNECT"
        if connect:
            if "/" in target or "@" in target:
                return
            parsed = urlsplit(f"https://{target}/")
            if not parsed.hostname or parsed.port is None or parsed.netloc != target:
                return
            host, port, url = parsed.hostname, parsed.port, parsed.geturl()
        else:
            parsed = urlsplit(target)
            if parsed.scheme not in ("http", "ws") or not parsed.hostname:
                return
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                return
            scheme = "http"
            host = parsed.hostname
            port = parsed.port or (443 if scheme == "https" else 80)
            url = parsed._replace(scheme=scheme).geturl()
            expected_authority = parsed.netloc.lower()
            if header.get("host", "").lower() != expected_authority:
                await self._deny(writer, host, url, "Host differs from proxy target")
                return
            if (
                "transfer-encoding" in header
                or int(header.get("content-length", "0")) > 10_000_000
            ):
                await self._deny(writer, host, url, "Unsupported request framing")
                return
        if not (0 < port < 65536):
            return
        try:
            decision = await asyncio.to_thread(evaluate, url, egress_policy_for(BROWSE))
        except Exception as exc:
            await self._deny(writer, host, url, f"Egress policy unavailable: {exc}")
            return
        if not decision.allow or not decision.pinned_ips:
            await self._deny(writer, host, url, decision.reason or "Egress denied")
            return
        upstream_reader = upstream_writer = None
        for ip in decision.pinned_ips:
            try:
                ipaddress.ip_address(ip)
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), 10
                )
                break
            except (OSError, ValueError, asyncio.TimeoutError):
                continue
        if upstream_writer is None or upstream_reader is None:
            await self._deny(
                writer, host, url, "Validated destination unavailable", status=502
            )
            return
        try:
            if connect:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self._tunnel(reader, writer, upstream_reader, upstream_writer)
            else:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                upgrade = header.get("upgrade", "").lower() == "websocket"
                upstream_writer.write(f"{method} {path} HTTP/1.1\r\n".encode("ascii"))
                for header_name, header_value in headers:
                    if header_name not in (
                        "proxy-connection",
                        "proxy-authorization",
                        "connection",
                        "content-length",
                    ):
                        upstream_writer.write(
                            f"{header_name}: {header_value}\r\n".encode("latin1")
                        )
                upstream_writer.write(
                    (
                        "Connection: Upgrade\r\n"
                        if upgrade
                        else "Connection: close\r\n"
                    ).encode()
                )
                length = int(header.get("content-length", "0"))
                if length:
                    upstream_writer.write(f"Content-Length: {length}\r\n".encode())
                upstream_writer.write(b"\r\n")
                await upstream_writer.drain()
                while length:
                    chunk = await reader.read(min(length, 65536))
                    if not chunk:
                        return
                    upstream_writer.write(chunk)
                    await upstream_writer.drain()
                    length -= len(chunk)
                if upgrade:
                    await self._tunnel(reader, writer, upstream_reader, upstream_writer)
                else:
                    while chunk := await upstream_reader.read(65536):
                        writer.write(chunk)
                        await writer.drain()
        finally:
            upstream_writer.close()
            with contextlib.suppress(OSError):
                await upstream_writer.wait_closed()

    async def _tunnel(
        self,
        left_read: asyncio.StreamReader,
        left_write: asyncio.StreamWriter,
        right_read: asyncio.StreamReader,
        right_write: asyncio.StreamWriter,
    ) -> None:
        async def copy(
            source: asyncio.StreamReader, destination: asyncio.StreamWriter
        ) -> None:
            while data := await source.read(65536):
                destination.write(data)
                await destination.drain()

        tasks = (
            asyncio.create_task(copy(left_read, right_write)),
            asyncio.create_task(copy(right_read, left_write)),
        )
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _deny(
        self,
        writer: asyncio.StreamWriter,
        host: str,
        url: str,
        reason: str,
        *,
        status: int = 403,
    ) -> None:
        try:
            SecurityEventLog().log(
                SecurityEvent(
                    event_id=uuid.uuid4().hex[:16],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    event_type="browse_egress",
                    caller_identity="customer_browser",
                    agent="gideon",
                    source="dashboard",
                    operation="proxy_connect",
                    tool_kind="browse_proxy",
                    outcome="denied",
                    resources=f"host={host}",
                    metadata={
                        "host": host,
                        "url": url,
                        "reason": reason,
                        "phase": "proxy",
                    },
                )
            )
        except Exception:
            logger.warning("customer browser proxy SEL write failed", exc_info=True)
        writer.write(
            f"HTTP/1.1 {status} Denied\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
