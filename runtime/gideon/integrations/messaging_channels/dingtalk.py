"""DingTalk Stream Mode ingress and authenticated robot replies."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from gideon.integrations.channel_transports.base import ChannelCapabilities, OutboundMessage
from gideon.integrations.messaging_channels.base import MessagingTransport

_OAUTH = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
_GROUP_SEND = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
_OTO_SEND = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"


class DingTalkTransport(MessagingTransport):
    name = "dingtalk"
    display_name = "DingTalk"
    required = ("client_id", "client_secret")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.http: httpx.AsyncClient | None = None
        self.stream: Any = None
        self.token = ""
        self.token_deadline = 0.0

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True)

    async def _access_token(self) -> str:
        if self.token and time.time() < self.token_deadline:
            return self.token
        if self.http is None:
            raise ConnectionError("DingTalk HTTP client is closed")
        response = await self.http.post(
            _OAUTH,
            json={"appKey": self.config["client_id"], "appSecret": self.config["client_secret"]},
        )
        response.raise_for_status()
        body = response.json()
        token = str(body.get("accessToken") or "")
        if not token:
            raise PermissionError("DingTalk did not issue an access token")
        self.token = token
        self.token_deadline = time.time() + max(30, int(body.get("expireIn", 7200)) - 60)
        return token

    async def _open(self) -> None:
        from dingtalk_stream import (
            AckMessage,
            CallbackHandler,
            Credential,
            DingTalkStreamClient,
        )
        from dingtalk_stream.chatbot import ChatbotMessage

        self.http = httpx.AsyncClient(timeout=20)
        try:
            await self._access_token()
        except Exception:
            await self.http.aclose()
            self.http = None
            raise

        transport = self

        class Handler(CallbackHandler):
            async def process(self, message: Any):
                raw = message.data if isinstance(message.data, dict) else {}
                parsed = ChatbotMessage.from_dict(raw)
                sender = str(
                    getattr(parsed, "sender_staff_id", None)
                    or getattr(parsed, "sender_id", None)
                    or ""
                )
                chat_type = str(raw.get("conversationType") or "1")
                chat = (
                    "group:" + str(raw.get("conversationId") or raw.get("openConversationId") or "")
                    if chat_type == "2"
                    else sender
                )
                text = str(getattr(getattr(parsed, "text", None), "content", "") or "").strip()
                if not text:
                    text = str((raw.get("text") or {}).get("content") or "").strip()
                if not text:
                    text = str(((raw.get("extensions") or {}).get("content") or {}).get("recognition") or "")
                await transport._inbound(
                    chat,
                    sender,
                    text,
                    message_id=str(raw.get("msgId") or raw.get("messageId") or ""),
                    is_dm=chat_type != "2",
                    metadata={"sender_name": str(getattr(parsed, "sender_nick", None) or sender)},
                )
                return AckMessage.STATUS_OK, "OK"

        self.stream = DingTalkStreamClient(Credential(self.config["client_id"], self.config["client_secret"]))
        self.stream.register_callback_handler(ChatbotMessage.TOPIC, Handler())
        self._connected = True
        self._detail = "Connected"

    async def _receive(self) -> None:
        while True:
            try:
                await self.stream.start()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._connected = False
                await asyncio.sleep(5)

    async def _close(self) -> None:
        socket = getattr(self.stream, "websocket", None)
        if socket is not None:
            await socket.close()
        self.stream = None
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    async def send(self, message: OutboundMessage) -> bool:
        if self.http is None:
            raise ConnectionError("DingTalk is disconnected")
        token = await self._access_token()
        target = message.channel_id
        body: dict[str, Any] = {
            "robotCode": self.config["client_id"],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps(
                {"text": message.text, "title": "Gideon Reply"}, ensure_ascii=False
            ),
        }
        if target.startswith("group:"):
            body["openConversationId"] = target[6:]
            url = _GROUP_SEND
        else:
            body["userIds"] = [target]
            url = _OTO_SEND
        response = await self.http.post(
            url,
            json=body,
            headers={"x-acs-dingtalk-access-token": token},
        )
        response.raise_for_status()
        outcome = response.json()
        if outcome.get("errcode") not in (None, 0):
            raise ConnectionError(f"DingTalk rejected message: {outcome['errcode']}")
        return True


def create_provider(config: dict[str, Any] | None = None) -> DingTalkTransport:
    return DingTalkTransport(config)
