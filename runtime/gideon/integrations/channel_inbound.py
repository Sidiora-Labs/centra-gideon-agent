"""Admit channel messages once, then dispatch through their linked conversation.

Trust policy and pairing redemption belong to ``guard_inbound``. This module keeps
its verdict by message identity and owns only session routing and task lifetime.
"""

from __future__ import annotations

from gideon.engine.turn_source import arrived_on

import asyncio
import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
            hold_for_owner = None
            if is_dm and _speaks_as_owner(provider):
                hold_for_owner = lambda: _hold_unknown_sender(state, provider, msg)
            decision = _decide(
                state,
                provider,
                msg,
                is_dm=is_dm,
                hold_for_owner=hold_for_owner,
            )
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
    state: Any,
    provider: str,
    msg: ChannelMessage,
    *,
    is_dm: bool,
    hold_for_owner: Callable[[], bool] | None = None,
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
        hold_for_owner=hold_for_owner,
    )


def _speaks_as_owner(provider: str) -> bool:
    from gideon.integrations.channel_transports import get_transport

    transport = get_transport(provider)
    if transport is None:
        return False
    try:
        return bool(getattr(transport.capabilities(), "speaks_as_owner", False))
    except Exception:
        logger.warning(
            "channel %s capabilities could not be read; suppressing stranger reply",
            provider,
            exc_info=True,
        )
        return True


def _hold_unknown_sender(state: Any, provider: str, msg: ChannelMessage) -> bool:
    from gideon.integrations.channel_transports import get_transport
    from gideon.integrations.inbox_providers.native_source import hold_from_someone_new

    transport = get_transport(provider)
    metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
    item = hold_from_someone_new(
        state,
        provider=provider,
        channel_name=str(getattr(transport, "display_name", "") or provider),
        channel_id=msg.channel_id,
        sender_id=msg.sender,
        sender_name=str(metadata.get("sender_name") or ""),
        subject=str(metadata.get("subject") or ""),
        text=msg.text,
        thread_id=msg.thread_id,
        message_id=msg.message_id,
        ts=float(msg.ts or 0),
    )
    return item is not None


@dataclass(frozen=True)
class _SessionIngress:
    state: Any
    provider: str
    message: ChannelMessage
    text: str

    def resolve(self) -> Any:
        thread = self.message.thread_id or self.message.channel_id
        linked = self.state.get_linked_session(thread, self.provider)
        if linked is not None:
            if not getattr(linked, "_app", ""):
                linked._app = self.provider
            self.state.link_channel(linked.key, thread, self.message.channel_id, self.provider)
            return linked
        created = self.state.get_or_create_session(app=self.provider)
        self.state.link_channel(created.key, thread, self.message.channel_id, self.provider)
        return created

    def record(self, session: Any) -> None:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        displayed = self.text
        for redact in (redact_exfiltration_urls, redact_credentials):
            displayed, _ = redact(displayed)
        from gideon.security.approval_answer import ingress_record, on_channel
        tenant = self.provider
        if self.provider in {"slack", "discord"}:
            from gideon.integrations.channel_delivery import raw_delivery_for
            delivery = raw_delivery_for(self.provider)
            identity = delivery.approval_identity(self.message.channel_id) if delivery is not None and hasattr(delivery, "approval_identity") else None
            if identity is not None:
                tenant = identity["tenant"]
        if self.provider == "telegram":
            from gideon.integrations.channel_transports import get_transport
            transport = get_transport(self.provider)
            slot = str(self.message.metadata.get("telegram_bot_id") or "primary")
            child = (getattr(transport, "bots", {}) or {}).get(slot)
            if child is not None and getattr(child, "connected", False):
                tenant = f"telegram:{child.slot}"
        origin = ingress_record(on_channel(self.provider, self.message.sender, tenant),
                                self.message.thread_id or self.message.channel_id, self.text)
        payload = {
            "session": session.key,
            "role": "user",
            "content": displayed,
            "cls": "msg msg-u",
            "meta": {"ingress": origin},
        }
        paths = [str(item["path"]) for item in self.message.attachments if item.get("path")]
        if paths:
            payload["meta"]["files"] = paths
            raw_files = [
                str(item["path"])
                for item in self.message.attachments
                if item.get("path") and item.get("skip_extract")
            ]
            if raw_files:
                payload["meta"]["raw_files"] = raw_files
            session.append(payload["role"], payload["content"], payload["cls"], meta=payload["meta"], source=arrived_on(self.message.thread_id or self.message.channel_id, self.message.sender))
        else:
            session.append(payload["role"], payload["content"], payload["cls"], meta=payload["meta"], source=arrived_on(self.message.thread_id or self.message.channel_id, self.message.sender))
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
            from gideon.security.approval_answer import ingress_record, on_channel
            tenant = self.provider
            if self.provider in {"slack", "discord"}:
                from gideon.integrations.channel_delivery import raw_delivery_for
                delivery = raw_delivery_for(self.provider)
                identity = delivery.approval_identity(self.message.channel_id) if delivery is not None and hasattr(delivery, "approval_identity") else None
                if identity is not None:
                    tenant = identity["tenant"]
            if self.provider == "telegram":
                from gideon.integrations.channel_transports import get_transport
                transport = get_transport(self.provider)
                slot = str(self.message.metadata.get("telegram_bot_id") or "primary")
                child = (getattr(transport, "bots", {}) or {}).get(slot)
                if child is not None and getattr(child, "connected", False):
                    tenant = f"telegram:{child.slot}"
            meta = {"ingress": ingress_record(on_channel(self.provider, self.message.sender, tenant),
                                            self.message.thread_id or self.message.channel_id, self.text)}
            queue_id = session.queue_append(self.text, channel=self.provider, meta=meta, source=arrived_on(self.message.thread_id or self.message.channel_id, self.message.sender))
            from gideon.security.security import (
                redact_credentials,
                redact_exfiltration_urls,
            )

            displayed = self.text
            for redact in (redact_exfiltration_urls, redact_credentials):
                displayed, _ = redact(displayed)
            broadcast = getattr(self.state, "broadcast_ws", None)
            if broadcast is not None:
                broadcast(
                    "queue_push",
                    {
                        "session": session.key,
                        "content": displayed,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "queue_id": queue_id,
                    },
                )
            refresh = getattr(self.state, "push_sessions_update", None)
            if refresh is not None:
                refresh()
            return

        self.record(session)
        from gideon.interfaces.dashboard.chat_runner import run_chat
        if runner is run_chat:
            running = runner(self.state, session, self.text, _origin_message=session.messages[-1])
        else:
            running = runner(self.state, session, self.text)
        pending = asyncio.ensure_future(running)
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
        ingress.dispatch(session, turn_runner)
    else:
        logger.warning(
            "channel inbound: no dashboard state — cannot route %s message", provider
        )
