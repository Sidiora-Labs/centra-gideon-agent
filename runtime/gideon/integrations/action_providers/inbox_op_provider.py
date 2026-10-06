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
    value = getattr(item, "thread_key", None)
    if isinstance(value, str) and value:
        return value
    identifier = str(getattr(item, "thread_ts", "") or getattr(item, "id", ""))
    return identifier.rsplit("_", 1)[-1]


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
        live.update(item, draft=draft, drafted_by="")
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
            live.update(item, draft=prior, drafted_by="")
        else:
            return ActionResult(False, error=f"inbox-op: cannot undo {operation!r}")
        return _result(dict(undone=operation, item_id=self.item_id))


async def _draft_through_the_inbox(state: Any, item: Any) -> str:
    """Draft a reply to *item* through the Inbox's own drafting path: ``""`` when it wrote one,
    else why it did not, in words the action's result can say.

    A message that takes no reply is refused before the model runs, as the Inbox page's Draft
    refuses it. A draft that did not happen changes nothing: the drafting path clears the draft
    when its model judges no reply is wanted, so the draft she had is put back, with what it
    stood on and who wrote it.
    """
    from gideon.integrations.inbox_service import InboxService

    if not getattr(item, "can_reply", False):
        return f"{str(item.id)} takes no reply, so there is no reply to draft"
    # Type-checked for `live_store`'s reason: an object answering every getattr must not stand
    # in for the service and swallow the draft.
    service = getattr(state, "_inbox_svc", None)
    if not isinstance(service, InboxService):
        return "no running inbox service to draft the reply with"
    before = {
        "draft": str(getattr(item, "draft", "") or ""),
        "drafted_by": str(getattr(item, "drafted_by", "") or ""),
        "context_summary": str(getattr(item, "context_summary", "") or ""),
    }
    outcome = await service.draft_reply(item.id)
    if outcome is None:
        return "the reply could not be drafted: the call to the drafting model failed"
    if outcome.unread:
        return outcome.unread_sentence()
    if outcome.question:
        return f"the reply needs your word first: {outcome.question}"
    if outcome.skipped or not str(getattr(outcome.item, "draft", "") or "").strip():
        service.inbox.update(item.id, **before)
        if outcome.skipped:
            return "the drafting model judged that this message needs no reply"
        return "the drafting model wrote no reply"
    return ""

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
        if operation == "reply_draft" and not str(action_config.get("draft") or action_config.get("body") or "").strip():
            prior = str(getattr(item, "draft", "") or "")
            reason = await _draft_through_the_inbox(live.state, item)
            if reason:
                return ActionResult(False, error="inbox-op: " + reason)
            updated = live.store.items.get(item_id)
            draft = str(getattr(updated, "draft", "") or "")
            return _result(dict(op=operation, item_id=item_id, drafted=len(draft)), dict(op=operation, item_id=item_id, prior=prior))
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
