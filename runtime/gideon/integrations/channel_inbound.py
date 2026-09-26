"""Admit channel messages once, then dispatch through their linked conversation.

Trust policy and pairing redemption belong to ``guard_inbound``. This module keeps
its verdict by message identity and owns only session routing and task lifetime.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, replace
from threading import RLock
from typing import TYPE_CHECKING, Any

from gideon.integrations.channel_trust import TrustVerdict, guard_inbound

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gideon.engine.gateway_services import GatewayServices
    from gideon.integrations.channel_transports.base import ChannelMessage

logger = logging.getLogger(__name__)
_ADMISSION_CACHE_MAX = 512
_ADMITTED: OrderedDict[str, TrustVerdict] = OrderedDict()
_DELIVERED: OrderedDict[str, None] = OrderedDict()
_ADMISSION_LOCK = RLock()


def _message_key(provider: str, msg: ChannelMessage) -> str:
    identity: tuple[str, ...]
    if msg.message_id:
        identity = (provider, "id", msg.channel_id, msg.message_id)
    else:
        body = (msg.text or "").encode("utf-8")
        fingerprint = hashlib.sha256(body).hexdigest()[:32]
        identity = (provider, "syn", msg.channel_id, str(msg.ts), fingerprint)
    return "|".join(identity)


def _remember(key: str, verdict: TrustVerdict) -> None:
    with _ADMISSION_LOCK:
        _ADMITTED[key] = verdict
        excess = len(_ADMITTED) - _ADMISSION_CACHE_MAX
        for _ in range(max(0, excess)):
            _ADMITTED.popitem(last=False)


def reset_admissions() -> None:
    with _ADMISSION_LOCK:
        _ADMITTED.clear()
        _DELIVERED.clear()


def _reserve_delivery(identity: str) -> bool:
    with _ADMISSION_LOCK:
        if identity in _DELIVERED:
            return False
        _DELIVERED[identity] = None
        while len(_DELIVERED) > _ADMISSION_CACHE_MAX:
            _DELIVERED.popitem(last=False)
        return True


def admit(
    state: Any, provider: str, msg: ChannelMessage, *, is_dm: bool = True
) -> TrustVerdict:
    """Return the first decision for an identity, without repeating gate effects."""
    identity = _message_key(provider, msg)
    with _ADMISSION_LOCK:
        if identity not in _ADMITTED:
            decision = _decide(state, provider, msg, is_dm=is_dm)
            _remember(identity, decision)
            return decision
        decision = _ADMITTED[identity]
    logger.debug(
        "channel inbound already decided: provider=%s reason=%s allowed=%s",
        provider,
        decision.reason,
        decision.allowed,
    )
    return replace(
        decision,
        canned_reply="",
        fired_notification=False,
        meta={**decision.meta, "duplicate": True},
    )


def _decide(
    state: Any, provider: str, msg: ChannelMessage, *, is_dm: bool
) -> TrustVerdict:
    metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
    return guard_inbound(
        state,
        provider,
        msg.sender,
        sender_name=str(metadata.get("sender_name") or ""),
        channel_id=msg.channel_id,
        is_dm=is_dm,
        text=msg.text,
    )


@dataclass(frozen=True)
class _SessionIngress:
    state: Any
    provider: str
    message: ChannelMessage
    text: str

    def resolve(self) -> Any:
        thread = self.message.thread_id or self.message.channel_id
        linked = self.state.get_linked_session(thread)
        if linked is not None:
            if not getattr(linked, "_app", ""):
                linked._app = self.provider
            self.state.link_channel(linked.key, thread, self.message.channel_id)
            return linked
        created = self.state.get_or_create_session(app=self.provider)
        self.state.link_channel(created.key, thread, self.message.channel_id)
        return created

    def record(self, session: Any) -> None:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        displayed = self.text
        for redact in (redact_exfiltration_urls, redact_credentials):
            displayed, _ = redact(displayed)
        payload = {
            "session": session.key,
            "role": "user",
            "content": displayed,
            "cls": "msg msg-u",
        }
        paths = [str(item["path"]) for item in self.message.attachments if item.get("path")]
        if paths:
            payload["meta"] = {"files": paths}
            raw_files = [
                str(item["path"])
                for item in self.message.attachments
                if item.get("path") and item.get("skip_extract")
            ]
            if raw_files:
                payload["meta"]["raw_files"] = raw_files
            session.append(payload["role"], payload["content"], payload["cls"], meta=payload["meta"])
        else:
            session.append(payload["role"], payload["content"], payload["cls"])
        broadcast = getattr(self.state, "broadcast_ws", None)
        if broadcast is not None:
            broadcast("chat_message", payload)
        refresh = getattr(self.state, "push_sessions_update", None)
        if refresh is not None:
            refresh()

    def dispatch(
        self, session: Any, runner: Callable[[Any, Any, str], Awaitable[None]]
    ) -> None:
        if getattr(session, "running", False):
            session.queue_append(self.text)
        else:
            pending = asyncio.ensure_future(runner(self.state, session, self.text))
            session.task = pending
            retained = getattr(self.state, "_background_tasks", None)
            if retained is not None:
                retained.add(pending)
                pending.add_done_callback(retained.discard)


async def deliver_inbound(
    services: GatewayServices,
    provider: str,
    msg: ChannelMessage,
    *,
    is_dm: bool = True,
    turn_runner: Callable[[Any, Any, str], Awaitable[None]],
) -> TrustVerdict:
    """Return the gate's verdict and deliver only explicitly admitted content."""
    decision = admit(
        getattr(services, "dashboard_state", None), provider, msg, is_dm=is_dm
    )
    if decision.allowed:
        identity = _message_key(provider, msg)
        if _reserve_delivery(identity):
            try:
                await _route_to_session(
                    services, provider, msg, decision.fenced_text or msg.text, turn_runner
                )
            except Exception:
                with _ADMISSION_LOCK:
                    _DELIVERED.pop(identity, None)
                raise
    return decision


async def _route_to_session(
    services: GatewayServices,
    provider: str,
    msg: ChannelMessage,
    text: str,
    turn_runner: Callable[[Any, Any, str], Awaitable[None]],
) -> None:
    state = getattr(services, "dashboard_state", None)
    if state is not None:
        ingress = _SessionIngress(state, provider, msg, text)
        session = ingress.resolve()
        ingress.record(session)
        ingress.dispatch(session, turn_runner)
    else:
        logger.warning(
            "channel inbound: no dashboard state — cannot route %s message", provider
        )
