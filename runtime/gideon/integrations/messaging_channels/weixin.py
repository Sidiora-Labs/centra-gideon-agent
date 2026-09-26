"""Personal WeChat iLink long polling with QR pairing and text replies."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from gideon.core.atomic_write import atomic_write
from gideon.extensions.providers.settings import ProviderSettings
from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport


def _print_pairing_qr(content: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(content)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


class WeixinTransport(MessagingTransport):
    name = "weixin"
    display_name = "WeChat"
    required = ("base_url",)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.http: httpx.AsyncClient | None = None
        self.base_url = ""
        self.token = ""
        self.cursor = ""
        self.context_tokens: dict[str, str] = {}
        self.seen: deque[str] = deque(maxlen=1000)
        self.pairing_qr = ""

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    async def health(self) -> dict[str, Any]:
        result = await super().health()
        if self.pairing_qr and not self._connected:
            result["state"] = "pairing"
            result["pairingQr"] = self.pairing_qr
            result["detail"] = "Scan the WeChat pairing QR"
        return result

    def _state_path(self) -> Path:
        return ProviderSettings.config_path("weixin-channel").parent / "account.json"

    def _save(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            path,
            json.dumps(
                {
                    "token": self.token,
                    "cursor": self.cursor,
                    "context_tokens": self.context_tokens,
                    "base_url": self.base_url,
                },
                ensure_ascii=False,
            ) + "\n",
        )
        path.chmod(0o600)

    def _headers(self, authenticated: bool = True) -> dict[str, str]:
        uin = base64.b64encode(str(int.from_bytes(os.urandom(4), "big")).encode()).decode()
        headers = {
            "X-WECHAT-UIN": uin,
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": str((2 << 16) | (1 << 8) | 1),
        }
        if authenticated and self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.config.get("route_tag"):
            headers["SKRouteTag"] = str(self.config["route_tag"])
        return headers

    def _trusted_base(self, value: str) -> str:
        candidate = value.strip().rstrip("/")
        if candidate and "://" not in candidate:
            candidate = "https://" + candidate
        parsed = urlparse(candidate)
        configured = urlparse(str(self.config["base_url"]))
        host = parsed.hostname or ""
        expected = configured.hostname or ""
        if parsed.scheme != "https" or not host:
            return ""
        if host == expected or (expected == "ilinkai.weixin.qq.com" and host.endswith(".weixin.qq.com")):
            return candidate
        return ""

    async def _get(self, endpoint: str, params: dict[str, Any], *, base: str = "") -> dict[str, Any]:
        if self.http is None:
            raise ConnectionError("WeChat client is closed")
        response = await self.http.get(
            (base or self.base_url) + "/" + endpoint,
            params=params,
            headers=self._headers(False),
        )
        response.raise_for_status()
        return response.json()

    async def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.http is None:
            raise ConnectionError("WeChat client is closed")
        response = await self.http.post(
            self.base_url + "/" + endpoint,
            json={**body, "base_info": {"channel_version": "2.1.1"}},
            headers=self._headers(),
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("WeChat response is not an object")
        return result

    async def _open(self) -> None:
        self.base_url = self._trusted_base(str(self.config["base_url"]))
        if not self.base_url:
            raise ValueError("WeChat base URL must be an allowed HTTPS operator endpoint")
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(50, connect=15), follow_redirects=False)
        try:
            stored = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        self.token = str(self.config.get("token") or stored.get("token") or "")
        self.cursor = str(stored.get("cursor") or "")
        contexts = stored.get("context_tokens") or {}
        self.context_tokens = {
            str(key): str(value)
            for key, value in contexts.items()
            if isinstance(contexts, dict) and key and value
        } if isinstance(contexts, dict) else {}
        if stored.get("base_url"):
            self.base_url = self._trusted_base(str(stored["base_url"])) or self.base_url
        self._connected = bool(self.token)
        self._detail = "Connected" if self.token else "Awaiting WeChat pairing"

    async def _pair(self) -> None:
        while not self.token:
            ticket = await self._get("ilink/bot/get_bot_qrcode", {"bot_type": "3"})
            code = str(ticket.get("qrcode") or "")
            if not code:
                raise ConnectionError("WeChat did not issue a pairing code")
            self.pairing_qr = str(ticket.get("qrcode_img_content") or code)
            _print_pairing_qr(self.pairing_qr)
            poll_base = self.base_url
            while not self.token:
                state = await self._get(
                    "ilink/bot/get_qrcode_status", {"qrcode": code}, base=poll_base
                )
                status = state.get("status")
                if status == "confirmed":
                    token = str(state.get("bot_token") or "")
                    if not token:
                        raise PermissionError("WeChat pairing confirmed without a token")
                    self.token = token
                    self.base_url = self._trusted_base(str(state.get("baseurl") or "")) or self.base_url
                    self.pairing_qr = ""
                    self._connected = True
                    self._detail = "Connected"
                    await asyncio.to_thread(self._save)
                    return
                if status == "scaned_but_redirect":
                    poll_base = self._trusted_base(str(state.get("redirect_host") or "")) or poll_base
                if status == "expired":
                    self.pairing_qr = ""
                    break
                await asyncio.sleep(1)

    async def _receive(self) -> None:
        while True:
            try:
                if not self.token:
                    await self._pair()
                data = await self._post("ilink/bot/getupdates", {"get_updates_buf": self.cursor})
                code = data.get("errcode") or data.get("ret") or 0
                if code == -14:
                    self.token = ""
                    self.cursor = ""
                    self.context_tokens.clear()
                    self._connected = False
                    await asyncio.to_thread(self._save)
                    continue
                if code:
                    raise ConnectionError(f"WeChat update request failed: {code}")
                for item in data.get("msgs") or []:
                    if isinstance(item, dict):
                        await self._message(item)
                if data.get("get_updates_buf"):
                    self.cursor = str(data["get_updates_buf"])
                    await asyncio.to_thread(self._save)
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                continue
            except Exception:
                self._connected = False
                self.pairing_qr = ""
                self._detail = "WeChat request failed; retrying"
                await asyncio.sleep(3)

    async def _message(self, payload: dict[str, Any]) -> None:
        if payload.get("message_type") == 2:
            return
        sender = str(payload.get("from_user_id") or "")
        message_id = str(payload.get("message_id") or payload.get("seq") or "")
        if not sender or not message_id or message_id in self.seen:
            return
        self.seen.append(message_id)
        if payload.get("context_token"):
            self.context_tokens[sender] = str(payload["context_token"])
            await asyncio.to_thread(self._save)
        parts: list[str] = []
        for item in payload.get("item_list") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == 1:
                parts.append(str((item.get("text_item") or {}).get("text") or ""))
            elif kind == 3:
                parts.append(str((item.get("voice_item") or {}).get("text") or "[voice]"))
            elif kind in (2, 4, 5):
                parts.append({2: "[image]", 4: "[file]", 5: "[video]"}[kind])
        text = "\n".join(part for part in parts if part).strip()
        self._connected = True
        self._detail = "Connected"
        await self._inbound(sender, sender, text, message_id=message_id, is_dm=True)

    async def _close(self) -> None:
        self.pairing_qr = ""
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    async def send(self, message: OutboundMessage) -> bool:
        if not self.token or self.http is None:
            raise ConnectionError("WeChat is not paired")
        context = self.context_tokens.get(message.channel_id)
        if not context:
            raise LookupError("WeChat requires an inbound context token before replying")
        for offset in range(0, len(message.text), 4000):
            body = {
                "from_user_id": "",
                "to_user_id": message.channel_id,
                "client_id": "gideon-" + uuid.uuid4().hex[:12],
                "message_type": 2,
                "message_state": 2,
                "context_token": context,
                "item_list": [
                    {"type": 1, "text_item": {"text": message.text[offset : offset + 4000]}}
                ],
            }
            result = await self._post("ilink/bot/sendmessage", {"msg": body})
            if result.get("errcode") or result.get("ret"):
                raise ConnectionError("WeChat rejected message")
        return True


def create_provider(config: dict[str, Any] | None = None) -> WeixinTransport:
    return WeixinTransport(config)
