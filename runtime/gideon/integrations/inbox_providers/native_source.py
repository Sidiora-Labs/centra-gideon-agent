"""Persist agent-authored inbox entries and announce them through the active UI."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Any

from gideon.integrations.inbox import (
    Classification,
    InboxItem,
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
    thread_mute_key,
)

logger = logging.getLogger(__name__)
SOURCE_NAME = "native"
_KIND_MAP = {
    "question": (Classification.NEEDS_REPLY.value, True),
    "notification": (Classification.FYI.value, False),
    "fyi": (Classification.FYI.value, False),
}
_dashboard_state = None
_push_lock = RLock()


def set_dashboard_state(state) -> None:
    global _dashboard_state
    with _push_lock:
        _dashboard_state = state


def get_dashboard_state():
    with _push_lock:
        return _dashboard_state


def _store_from_state(state) -> InboxStore:
    with _push_lock:
        service = getattr(state, "_inbox_svc", None)
        if service is not None:
            return service.inbox
        existing = getattr(state, "_inbox_store", None)
        if existing is not None:
            return existing
        loaded = InboxStore()
        loaded.load()
        state._inbox_store = loaded
        return loaded


def _state_from_state(state) -> InboxState:
    with _push_lock:
        service = getattr(state, "_inbox_svc", None)
        if service is not None:
            return service.state
        existing = getattr(state, "_inbox_state", None)
        if existing is not None:
            return existing
        loaded = InboxState()
        loaded.load()
        state._inbox_state = loaded
        return loaded


def hold_from_someone_new(
    state: Any,
    *,
    provider: str,
    channel_name: str,
    channel_id: str,
    sender_id: str,
    text: str,
    sender_name: str = "",
    subject: str = "",
    thread_id: str = "",
    message_id: str = "",
    ts: float = 0.0,
) -> InboxItem | None:
    """Persist one unknown owner-voice message without dispatching it to an agent."""
    if state is None or not sender_id or not channel_id:
        return None
    import hashlib

    stamp = float(ts or 0) or time.time()
    identity = "\0".join(
        (provider, channel_id, message_id)
        if message_id
        else (provider, channel_id, sender_id, repr(stamp), thread_id, text)
    )
    key = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:20]
    item_id = f"someone_new_{key}_{stamp:.6f}"
    source = f"channel:{provider}"
    store = _store_from_state(state)
    inbox_state = _state_from_state(state)
    mute_key = thread_mute_key(source, channel_id, thread_id or message_id or item_id)
    if (
        item_id in store.items
        or item_id in inbox_state.dismissed
        or mute_key in inbox_state.muted_threads
    ):
        return None

    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    displayed = text
    for redact in (redact_exfiltration_urls, redact_credentials):
        displayed, _ = redact(displayed)
    title = str(subject or "").strip()
    item = InboxItem(
        id=item_id,
        channel=channel_id,
        channel_name="DM",
        thread_ts=thread_id or None,
        message=f"{title}\n\n{displayed}" if title else displayed,
        sender_id=sender_id,
        sender_name=sender_name or sender_id,
        status=ItemStatus.PENDING.value,
        created_at=stamp,
        source=source,
        can_reply=True,
        reply_target=message_id or thread_id,
        item_kind=ItemKind.MESSAGE.value,
        refs={"someone_new": provider, "channel_name": channel_name},
    )
    store.add(item)
    store.flush()
    try:
        from gideon.integrations.inbox import redact_item as _redact_item

        state.broadcast_ws("inbox_new_item", _redact_item(item.to_dict()))
    except Exception:
        logger.debug("hold_from_someone_new: broadcast failed", exc_info=True)
    sorter = getattr(getattr(state, "_inbox_svc", None), "sorter", None)
    if sorter is not None:
        sorter.wake()
    return item


@dataclass(frozen=True)
class _NativeMessage:
    message: str
    kind: str
    sender: str
    context: str | None
    reply_target: str

    def item(self) -> InboxItem:
        classification, replyable = _KIND_MAP.get(self.kind, _KIND_MAP["notification"])
        created = time.time()
        identity = "agent_" + format(created, ".6f") + "-" + uuid.uuid4().hex[:6]
        attributes: dict = dict(
            id=identity,
            channel="agent",
            channel_name="agent",
            thread_ts=None,
            message=self.message,
            sender_id=self.sender,
            sender_name=self.sender,
            classification=classification,
            status=ItemStatus.PENDING.value,
            created_at=created,
            context_summary=self.context or "",
            source=SOURCE_NAME,
            can_reply=replyable,
            reply_target=self.reply_target if replyable else "",
        )
        return InboxItem(**attributes)


def _evaluate_item_alert(state: Any, item: InboxItem) -> None:
    from gideon.core.config.loader import AppConfig
    from gideon.integrations.inbox import evaluate_alert, notify_inbox_alert

    reason = evaluate_alert(item, AppConfig.load().dashboard.user_name or "")
    if reason:
        notify_inbox_alert(state, item, reason)


def _announce_item(state: Any, item: InboxItem) -> None:
    from gideon.integrations.inbox import redact_item as _redact_item

    state.broadcast_ws("inbox_new_item", _redact_item(item.to_dict()))


def post_to_inbox(
    message: str,
    *,
    kind: str = "notification",
    sender_name: str = "agent",
    context: str | None = None,
    reply_target: str = "",
    state=None,
) -> InboxItem | None:
    target = state or get_dashboard_state()
    if target is None:
        logger.debug("post_to_inbox: no dashboard state wired; dropping item")
        return None
    item = _NativeMessage(message, kind, sender_name, context, reply_target).item()
    with _push_lock:
        store = _store_from_state(target)
        store.add(item)
        store.flush()
    for label, publish in (
        ("alert evaluation", _evaluate_item_alert),
        ("broadcast", _announce_item),
    ):
        try:
            publish(target, item)
        except Exception:
            logger.debug("post_to_inbox: %s failed", label, exc_info=True)
    return item


async def open_inbox_items(reader, *, kind=""):
    """Read active native rows without changing anyone's seen state."""
    state = get_dashboard_state()
    if state is None or not reader.admitted:
        return None
    from gideon.integrations.inbox import owner_username

    store = _store_from_state(state)
    store.flush()
    store.load()
    items = [
        item
        for item in store.open_items(owner_username())
        if reader.reads(item) and (not kind or item.item_kind == kind)
    ]
    return sorted(items, key=lambda item: item.created_at, reverse=True)
