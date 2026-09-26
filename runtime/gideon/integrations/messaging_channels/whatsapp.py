"""WhatsApp Web messaging through a token-authenticated local or managed bridge."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets

from gideon.core.atomic_write import atomic_write
from gideon.extensions.providers.settings import ProviderSettings
from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport


class WhatsAppTransport(MessagingTransport):
    name = "whatsapp"
    display_name = "WhatsApp"
    required = ("bridge_url",)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.ws: Any = None
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[str, asyncio.Future[str]] = {}
        self.pairing_qr = ""
        self.bridge_token = ""

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    async def health(self) -> dict[str, Any]:
        result = await super().health()
        if self.pairing_qr and not self._connected:
            result["state"] = "pairing"
            result["pairingQr"] = self.pairing_qr
            result["detail"] = "Scan the pairing QR with WhatsApp Linked Devices"
        return result

    def _settings_dir(self) -> Path:
        return ProviderSettings.config_path("whatsapp-channel").parent

    async def _open(self) -> None:
        url = urlparse(str(self.config["bridge_url"]))
        local = url.hostname in {"127.0.0.1", "localhost", "::1"}
        if url.scheme != "wss" and not (local and url.scheme == "ws"):
            raise ValueError("WhatsApp bridge must use wss, or ws on loopback")
        self.bridge_token = str(self.config.get("bridge_token") or "")
        if local and bool(self.config.get("auto_bridge", True)):
            directory = self._settings_dir()
            directory.mkdir(parents=True, exist_ok=True)
            if not self.bridge_token:
                token_path = directory / "bridge-token"
                try:
                    self.bridge_token = token_path.read_text(encoding="utf-8").strip()
                except OSError:
                    self.bridge_token = secrets.token_urlsafe(48)
                    atomic_write(token_path, self.bridge_token + "\n")
                    token_path.chmod(0o600)
            script = Path(__file__).parent / "bridge" / "index.mjs"
            dependencies = script.parent / "node_modules" / "@whiskeysockets" / "baileys"
            node = shutil.which("node")
            if not node or not dependencies.is_dir():
                raise RuntimeError("Install Node 20+ and the WhatsApp bridge package dependencies")
            port = url.port or 3001
            environment = os.environ.copy()
            environment.update(
                GIDEON_BRIDGE_PORT=str(port),
                GIDEON_BRIDGE_TOKEN=self.bridge_token,
                GIDEON_BRIDGE_AUTH_DIR=str(directory / "auth"),
            )
            self.process = await asyncio.create_subprocess_exec(
                node,
                str(script),
                env=environment,
            )
        if not self.bridge_token:
            raise ValueError("A bridge token is required for remote WhatsApp bridges")
        self._detail = "Connecting to WhatsApp bridge"

    async def _receive(self) -> None:
        while True:
            try:
                async with websockets.connect(str(self.config["bridge_url"])) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"type": "auth", "token": self.bridge_token}))
                    async for frame in ws:
                        await self._frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._connected = False
                self.pairing_qr = ""
                self._detail = "WhatsApp bridge disconnected; reconnecting"
            finally:
                self.ws = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("WhatsApp bridge disconnected"))
                self.pending.clear()
            await asyncio.sleep(5)

    async def _frame(self, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            return
        kind = frame.get("type")
        if kind == "status":
            self._connected = frame.get("status") == "connected"
            self._detail = "Connected" if self._connected else "Awaiting WhatsApp pairing"
            if self._connected:
                self.pairing_qr = ""
        elif kind == "qr":
            self.pairing_qr = str(frame.get("qr") or "")
        elif kind in {"sent", "send_error"}:
            future = self.pending.pop(str(frame.get("id") or ""), None)
            if future is not None and not future.done():
                if kind == "sent":
                    future.set_result(str(frame.get("messageId") or ""))
                else:
                    future.set_exception(ConnectionError(str(frame.get("error") or "WhatsApp send failed")))
        elif kind == "message":
            chat = str(frame.get("chat") or "")
            group = bool(frame.get("isGroup"))
            if group and self.config.get("group_policy", "mention") == "mention" and not frame.get("wasMentioned"):
                return
            await self._inbound(
                chat,
                str(frame.get("sender") or ""),
                str(frame.get("content") or ""),
                message_id=str(frame.get("id") or ""),
                is_dm=not group,
                metadata={"sender_name": str(frame.get("sender") or "")},
            )

    async def _close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
        if self.process is not None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
            self.process = None

    async def send(self, message: OutboundMessage) -> str:
        if not self._connected or self.ws is None:
            raise ConnectionError("WhatsApp is not paired")
        identifier = uuid.uuid4().hex
        result: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending[identifier] = result
        await self.ws.send(
            json.dumps(
                {"type": "send", "id": identifier, "to": message.channel_id, "text": message.text},
                ensure_ascii=False,
            )
        )
        try:
            delivered_id = await asyncio.wait_for(result, timeout=20)
        finally:
            self.pending.pop(identifier, None)
        if not delivered_id:
            raise ConnectionError("WhatsApp bridge did not return a message receipt")
        return delivered_id


def create_provider(config: dict[str, Any] | None = None) -> WhatsAppTransport:
    return WhatsAppTransport(config)
