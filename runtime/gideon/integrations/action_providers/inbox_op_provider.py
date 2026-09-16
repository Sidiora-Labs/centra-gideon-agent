"""Reversible commands against the inbox handles owned by the running service."""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.inbox import ItemStatus, live_state, live_store, redact_item

logger = logging.getLogger(__name__)
HANDLE_KIND = "inbox-op"
_STATUS_OPS: dict[str, str] = {
    "archive": ItemStatus.HANDLED.value,
    "mark_read": ItemStatus.SEEN.value,
    "dismiss": ItemStatus.DISMISSED.value,
}
OPS: frozenset[str] = frozenset({*_STATUS_OPS, "mute_thread", "reply_draft"})


def _status_value(item: Any) -> str:
    value = getattr(item, "status", "")
    return value.value if isinstance(value, ItemStatus) else str(value or "")


def _thread_key(item: Any) -> str:
    if value := getattr(item, "thread_ts", None):
        return str(value)
    identifier = str(getattr(item, "id", "") or "")
    head, separator, tail = identifier.partition("_")
    return tail if separator else head


def _encode(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode()
    return HANDLE_KIND + ":" + base64.urlsafe_b64encode(serialized).decode()


def _decode(handle: str) -> dict[str, Any] | None:
    prefix, separator, encoded = (handle or "").partition(":")
    if prefix != HANDLE_KIND or not separator or not encoded:
        return None
    try:
        document = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _broadcast(state: Any, item: Any) -> None:
    try:
        state.broadcast_ws("inbox_item_updated", redact_item(item.to_dict()))
    except Exception:
        logger.debug("inbox-op: broadcast failed", exc_info=True)


def _result(
    payload: dict[str, Any], undo: dict[str, Any] | None = None
) -> ActionResult:
    return ActionResult(
        True,
        stdout=json.dumps(payload),
        reversal=_encode(undo) if undo is not None else "",
    )


@dataclass(frozen=True)
class _LiveInbox:
    state: Any
    store: Any

    @classmethod
    def resolve(cls) -> _LiveInbox | None:
        from gideon.integrations.action_providers.services import get_action_services

        state = getattr(get_action_services(), "state", None)
        store = live_store(state) if state is not None else None
        return None if store is None else cls(state, store)

    def update(self, item: Any, **fields: Any) -> None:
        self.store.update(item.id, **fields)
        _broadcast(self.state, self.store.items.get(item.id) or item)

    def dismissal(self, item_id: str, dismissed: bool) -> None:
        flags = live_state(self.state)
        if flags is None:
            return
        change = flags.dismissed.add if dismissed else flags.dismissed.discard
        change(item_id)
        flags.save()


@dataclass(frozen=True)
class _InboxCommand:
    operation: str
    item_id: str

    def apply(
        self, live: _LiveInbox, item: Any, config: dict[str, Any]
    ) -> ActionResult:
        operation = self.operation
        identity = dict(op=operation, item_id=self.item_id)
        if operation in _STATUS_OPS:
            prior = _status_value(item)
            target = _STATUS_OPS[operation]
            if prior == target:
                return _result({**identity, "changed": False})
            live.store.update(self.item_id, status=target)
            if operation == "dismiss":
                live.dismissal(self.item_id, True)
            _broadcast(live.state, live.store.items.get(self.item_id) or item)
            return _result({**identity, "changed": True}, {**identity, "prior": prior})
        if operation == "mute_thread":
            flags = live_state(live.state)
            if flags is None:
                return ActionResult(
                    False,
                    error="inbox-op: no running inbox service, so the mute set is unreachable",
                )
            thread = _thread_key(item)
            changed = thread not in flags.muted_threads
            if changed:
                flags.muted_threads.add(thread)
                flags.save()
            return _result(
                dict(op=operation, thread=thread, changed=changed),
                {**identity, "thread": thread} if changed else None,
            )
        draft = str(config.get("draft") or config.get("body") or "").strip()
        if not draft:
            return ActionResult(False, error="inbox-op: reply_draft needs a draft body")
        prior = str(getattr(item, "draft", "") or "")
        live.update(item, draft=draft)
        return _result(
            {**identity, "drafted": len(draft)}, {**identity, "prior": prior}
        )

    def restore(self, live: _LiveInbox, payload: dict[str, Any]) -> ActionResult:
        operation = self.operation
        if operation == "mute_thread":
            flags = live_state(live.state)
            thread = str(payload.get("thread") or "")
            if flags is None or thread not in flags.muted_threads:
                return ActionResult(
                    False, error="inbox-op: that thread is no longer muted"
                )
            flags.muted_threads.discard(thread)
            flags.save()
            return _result(dict(undone=operation, thread=thread))
        item = live.store.items.get(self.item_id)
        if item is None:
            return ActionResult(
                False,
                error=f"inbox-op: inbox item {self.item_id!r} is gone, so there is nothing to restore",
            )
        prior = str(payload.get("prior") or "")
        if operation in _STATUS_OPS:
            expected = _STATUS_OPS[operation]
            if _status_value(item) != expected:
                return ActionResult(
                    False,
                    error=f"inbox-op: {self.item_id!r} is no longer {expected!r}, so undoing the {operation} would overwrite a newer change",
                )
            live.store.update(self.item_id, status=prior or ItemStatus.PENDING.value)
            if operation == "dismiss":
                live.dismissal(self.item_id, False)
            _broadcast(live.state, live.store.items.get(self.item_id) or item)
        elif operation == "reply_draft":
            live.update(item, draft=prior)
        else:
            return ActionResult(False, error=f"inbox-op: cannot undo {operation!r}")
        return _result(dict(undone=operation, item_id=self.item_id))


class InboxOpActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "inbox-op"

    @property
    def display_name(self) -> str:
        return "Inbox Operation"

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        return (HANDLE_KIND,)

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        operation = str(
            action_config.get("op") or action_config.get("action_type") or ""
        ).strip()
        item_id = str(action_config.get("item_id") or "").strip()
        if operation not in OPS:
            return ActionResult(
                False,
                error=f"inbox-op: unknown op {operation!r} (expected one of {', '.join(sorted(OPS))})",
            )
        if not item_id:
            return ActionResult(False, error="inbox-op: item_id is required")
        live = _LiveInbox.resolve()
        if live is None:
            return ActionResult(
                False,
                error="inbox-op: no running inbox service — an operation written to a store the service cannot see would be overwritten by its next save",
            )
        item = live.store.items.get(item_id)
        if item is None:
            return ActionResult(False, error=f"inbox-op: no inbox item {item_id!r}")
        return _InboxCommand(operation, item_id).apply(live, item, action_config)

    async def reverse(self, handle: str) -> ActionResult:
        payload = _decode(handle)
        if payload is None:
            return ActionResult(False, error="inbox-op: unrecognised reversal handle")
        command = _InboxCommand(
            str(payload.get("op") or ""), str(payload.get("item_id") or "")
        )
        live = _LiveInbox.resolve()
        if live is None:
            return ActionResult(
                False, error="inbox-op: no running inbox service to undo against"
            )
        return command.restore(live, payload)


def create_provider(config: dict[str, Any] | None = None) -> InboxOpActionProvider:
    return InboxOpActionProvider()
