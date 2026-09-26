"""MoChat session and panel messaging through its authenticated HTTP API."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from gideon.extensions.providers.settings import ProviderSettings
from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport


class MoChatTransport(MessagingTransport):
    name = "mochat"
    display_name = "MoChat"
    required = ("base_url", "claw_token")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.http: httpx.AsyncClient | None = None
        self.targets: list[tuple[str, str]] = []
        self.cursors: dict[str, int] = {}
        self.seen: deque[str] = deque(maxlen=2000)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    def _state_path(self) -> Path:
        return ProviderSettings.config_path("mochat-channel").parent / "cursors.json"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.http is None:
            raise ConnectionError("MoChat HTTP client is closed")
        response = await self.http.post(
            str(self.config["base_url"]).rstrip("/") + path,
            json=payload,
            headers={"X-Claw-Token": str(self.config["claw_token"])},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("MoChat response is not an object")
        if body.get("code") not in (None, 200):
            raise ConnectionError(f"MoChat API rejected request: {body['code']}")
        data = body.get("data", body)
        return data if isinstance(data, dict) else {}

    async def _open(self) -> None:
        self.http = httpx.AsyncClient(timeout=35)
        try:
            await self._post("/api/claw/sessions/list", {})
        except Exception:
            await self.http.aclose()
            self.http = None
            raise
        self.targets = [
            (kind, value.strip())
            for kind, key in (("session", "sessions"), ("panel", "panels"))
            for value in str(self.config.get(key) or "").split(",")
            if value.strip()
        ]
        if not self.targets:
            await self.http.aclose()
            self.http = None
            raise ValueError("Configure at least one MoChat session or panel")
        try:
            payload = json.loads(self._state_path().read_text(encoding="utf-8"))
            self.cursors = {str(k): int(v) for k, v in payload.items()}
        except (OSError, ValueError, TypeError):
            self.cursors = {}
        self._connected = True
        self._detail = "Connected"

    def _save_cursors(self) -> None:
        from gideon.core.atomic_write import atomic_write

        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(self.cursors, sort_keys=True) + "\n")

    async def _receive(self) -> None:
        await asyncio.gather(*(self._watch(kind, target) for kind, target in self.targets))

    async def _watch(self, kind: str, target: str) -> None:
        while True:
            try:
                if kind == "session":
                    payload = await self._post(
                        "/api/claw/sessions/watch",
                        {
                            "sessionId": target,
                            "cursor": self.cursors.get(target, 0),
                            "timeoutMs": 25000,
                            "limit": 100,
                        },
                    )
                    for event in payload.get("events", []):
                        if isinstance(event, dict) and event.get("type") == "message.add":
                            await self._inbound_event(kind, target, event.get("payload") or {})
                    cursor = payload.get("cursor")
                    if isinstance(cursor, int) and cursor >= self.cursors.get(target, 0):
                        self.cursors[target] = cursor
                        await asyncio.to_thread(self._save_cursors)
                else:
                    payload = await self._post(
                        "/api/claw/groups/panels/messages",
                        {"panelId": target, "limit": 100},
                    )
                    for message in reversed(payload.get("messages") or []):
                        if isinstance(message, dict):
                            await self._inbound_event(kind, target, message)
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._connected = False
                self._detail = "MoChat request failed; retrying"
                await asyncio.sleep(3)

    async def _inbound_event(self, kind: str, target: str, payload: dict[str, Any]) -> None:
        sender = str(payload.get("author") or "")
        if not sender or sender == self.config.get("agent_user_id"):
            return
        message_id = str(payload.get("messageId") or "")
        seen_key = f"{kind}:{target}:{message_id}"
        if not message_id or seen_key in self.seen:
            return
        self.seen.append(seen_key)
        text = payload.get("content")
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False) if text is not None else ""
        self._connected = True
        self._detail = "Connected"
        await self._inbound(
            target,
            sender,
            text,
            message_id=seen_key,
            is_dm=kind == "session",
            metadata={"sender_name": sender},
        )

    async def _close(self) -> None:
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    async def send(self, message: OutboundMessage) -> bool:
        target = message.channel_id
        kind = next((kind for kind, value in self.targets if value == target), "session")
        if kind == "panel":
            await self._post("/api/claw/groups/panels/send", {"panelId": target, "content": message.text})
        else:
            await self._post("/api/claw/sessions/send", {"sessionId": target, "content": message.text})
        return True


def create_provider(config: dict[str, Any] | None = None) -> MoChatTransport:
    return MoChatTransport(config)
