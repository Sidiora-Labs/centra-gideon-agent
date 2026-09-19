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
    Confidence,
    InboxItem,
    InboxStore,
    ItemStatus,
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
            confidence=Confidence.HIGH.value,
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
    from gideon.interfaces.dashboard.handlers_inbox import _redact_item

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
