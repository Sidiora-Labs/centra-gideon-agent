"""Durable, redacted records for calls that ended without an owner answer."""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReentryAttempt:
    state: Any
    note_id: str
    origin_kind: str
    origin_id: str
    attempt_id: str
    fingerprint: str
    node_id: str = ""
    session: str = ""
    consumed: bool = False


_ACTIVE_REENTRY: ContextVar[ReentryAttempt | None] = ContextVar(
    "gideon_auto_denied_reentry", default=None
)


def call_fingerprint(tool: str, tool_input: Any) -> str:
    """Return a full digest of the canonical raw call without retaining its arguments."""
    if not isinstance(tool, str) or not tool or tool_input is None:
        return ""
    value = tool_input
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
    try:
        canonical = json.dumps(
            {"tool": tool, "input": value},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _origin_refs(session: str, source: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    if session:
        refs["chat"] = session
        for prefix in ("workflow:", "run:"):
            if session.startswith(prefix):
                _, _, tail = session.partition(":")
                run_id, separator, node_id = tail.partition(":")
                if run_id:
                    refs["run"] = run_id
                if separator and node_id:
                    refs["node"] = node_id
                break
        if session.startswith("trigger:"):
            refs["trigger"] = session.partition(":")[2]
    if source.startswith("trigger:"):
        refs["trigger"] = source.partition(":")[2].partition(":")[0]
    elif source.startswith(("workflow:", "run:")):
        _, _, tail = source.partition(":")
        run_id, separator, node_id = tail.partition(":")
        if run_id:
            refs.setdefault("run", run_id)
        if separator and node_id:
            refs.setdefault("node", node_id)
    return refs


def record_auto_denial(
    state: Any,
    *,
    session: str,
    call_id: str,
    tool: str,
    fingerprint: str,
    reason: str,
    source: str = "",
) -> str:
    """Persist a single redacted Inbox row; raw call arguments are never accepted here."""
    if reason not in {"expired", "unattended"}:
        return ""
    try:
        from gideon.integrations.inbox import ItemKind, emit_attention_item
        from gideon.security.security import redact_credentials, redact_exfiltration_urls

        safe_tool, _ = redact_exfiltration_urls(tool or "a tool")
        safe_tool, _ = redact_credentials(safe_tool)
        origin = _origin_refs(str(session or ""), str(source or ""))
        run_id = origin.get("run", "")
        if run_id:
            try:
                from gideon.automation.workflows import store as workflow_store

                workflow_run = workflow_store.get(run_id)
                run_origin = getattr(workflow_run, "origin", None)
                trigger_id = str(getattr(run_origin, "trigger_id", "") or "")
                if trigger_id:
                    origin["trigger"] = trigger_id
            except Exception:
                logger.debug("could not project persisted workflow trigger origin", exc_info=True)
        refs: dict[str, Any] = {
            "auto_denied": True,
            "reason": reason,
            "call_id": str(call_id or ""),
            "call_fingerprint": fingerprint,
            **origin,
        }
        safe_session = str(session or "")
        dedup = (
            f"auto-denied:{safe_session}:{origin.get('trigger', '')}:{origin.get('run', '')}:{origin.get('node', '')}:{fingerprint}"
            if fingerprint
            else f"auto-denied:{safe_session}:{origin.get('trigger', '')}:{origin.get('run', '')}:{origin.get('node', '')}:{call_id}:{reason}"
        )
        if reason == "expired":
            title = f"Unanswered approval: {safe_tool}"
            body = "The approval expired without an owner answer. Re-entry is limited to the recorded origin."
        else:
            title = f"Call blocked: {safe_tool}"
            body = "This unattended call needed owner approval and was blocked. Re-entry is limited to the recorded origin."
        return emit_attention_item(
            state,
            source="system",
            kind="agent_request",
            title=title,
            body=body,
            refs=refs,
            item_kind=ItemKind.AGENT_REQUEST.value,
            dedup_key=dedup,
        )
    except Exception:
        logger.debug("could not persist auto-denied call", exc_info=True)
        return ""


def unanswered_note(state: Any, note_id: str) -> Any | None:
    """Return the exact still-open denial row for its current owner."""
    try:
        from gideon.integrations.inbox import is_open_status, live_store, owner_username

        store = live_store(state)
        if store is None:
            return None
        owner = owner_username()
        item = store.items.get(note_id)
        if item is None or not is_open_status(item.status_for(owner)):
            return None
        refs = item.refs if isinstance(item.refs, dict) else {}
        if refs.get("auto_denied") is not True:
            return None
        return item
    except Exception:
        logger.debug("could not inspect auto-denied Inbox row", exc_info=True)
        return None


def unanswered_for_chat(state: Any, note_id: str, session: str) -> tuple[Any, str] | None:
    """Return the exact still-open denial row and digest for its bound chat."""
    item = unanswered_note(state, note_id)
    refs = item.refs if item is not None and isinstance(item.refs, dict) else {}
    fingerprint = refs.get("call_fingerprint")
    if refs.get("chat") != session or not isinstance(fingerprint, str) or len(fingerprint) != 64:
        return None
    return item, fingerprint


def _origin_matches(refs: dict[str, Any], origin_kind: str, origin_id: str, node_id: str) -> bool:
    if origin_kind == "trigger":
        return refs.get("trigger") == origin_id
    if origin_kind == "workflow":
        return refs.get("run") == origin_id and refs.get("node") == node_id and bool(node_id)
    return False


@contextmanager
def owner_reentry_attempt(
    state: Any,
    note_id: str,
    principal: Any,
    *,
    origin_kind: str,
    origin_id: str,
    attempt_id: str,
    node_id: str = "",
    session: str = "",
):
    """Scope one already-authorized owner action to its actual execution attempt."""
    from gideon.security.approval_answer import OWNER

    item = unanswered_note(state, note_id)
    refs = item.refs if item is not None and isinstance(item.refs, dict) else {}
    fingerprint = refs.get("call_fingerprint")
    if (
        principal.kind != OWNER
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or not attempt_id
        or not _origin_matches(refs, origin_kind, origin_id, node_id)
    ):
        yield None
        return
    binding = ReentryAttempt(
        state=state,
        note_id=note_id,
        origin_kind=origin_kind,
        origin_id=origin_id,
        attempt_id=attempt_id,
        fingerprint=fingerprint,
        node_id=node_id,
        session=session,
    )
    token = _ACTIVE_REENTRY.set(binding)
    try:
        yield binding
    finally:
        _ACTIVE_REENTRY.reset(token)


def bind_reentry_call(
    state: Any,
    fingerprint: str,
    *,
    origin_kind: str,
    origin_id: str,
    attempt_id: str,
    node_id: str = "",
    session: str = "",
) -> ReentryAttempt | None:
    """Consume the active accepted re-entry link for one matching real call."""
    binding = _ACTIVE_REENTRY.get()
    if (
        binding is None
        or binding.state is not state
        or binding.consumed
        or binding.fingerprint != fingerprint
        or binding.origin_kind != origin_kind
        or binding.origin_id != origin_id
        or binding.attempt_id != attempt_id
        or binding.node_id != node_id
        or (binding.session and binding.session != session)
    ):
        return None
    item = unanswered_note(state, binding.note_id)
    refs = item.refs if item is not None and isinstance(item.refs, dict) else {}
    if not _origin_matches(refs, origin_kind, origin_id, node_id):
        return None
    binding.consumed = True
    return binding


def bind_current_reentry_call(
    state: Any, fingerprint: str, *, session: str = ""
) -> ReentryAttempt | None:
    """Bind a matching call using only the active server-side attempt context."""
    binding = _ACTIVE_REENTRY.get()
    if binding is None:
        return None
    return bind_reentry_call(
        state,
        fingerprint,
        origin_kind=binding.origin_kind,
        origin_id=binding.origin_id,
        attempt_id=binding.attempt_id,
        node_id=binding.node_id,
        session=session or binding.session,
    )


def settle_answered_call(
    state: Any,
    *,
    note_id: str,
    session: str,
    fingerprint: str,
    outcome: str,
    principal: Any,
    origin_kind: str = "",
    origin_id: str = "",
    attempt_id: str = "",
    node_id: str = "",
) -> bool:
    """Close only the exact expired note after the existing owner/channel check succeeds."""
    from gideon.security.approval_answer import CHANNEL, OWNER

    if principal.kind not in {OWNER, CHANNEL} or outcome not in {"approved", "rejected"}:
        return False
    if origin_kind:
        item = unanswered_note(state, note_id)
        refs = item.refs if item is not None and isinstance(item.refs, dict) else {}
        stored_fingerprint = refs.get("call_fingerprint", "")
        if (
            not attempt_id
            or not _origin_matches(refs, origin_kind, origin_id, node_id)
        ):
            return False
    elif session:
        found = unanswered_for_chat(state, note_id, session)
        if found is None:
            return False
        item, stored_fingerprint = found
    else:
        item = unanswered_note(state, note_id)
        refs = item.refs if item is not None and isinstance(item.refs, dict) else {}
        stored_fingerprint = refs.get("call_fingerprint", "")
        if (
            not attempt_id
            or not _origin_matches(refs, origin_kind, origin_id, node_id)
        ):
            return False
    if stored_fingerprint != fingerprint:
        return False
    try:
        from gideon.integrations.inbox import ItemStatus, live_store, owner_username
        from gideon.interfaces.dashboard.handlers_inbox import _owner_item

        store = live_store(state)
        if store is None:
            return False
        owner = owner_username()
        status = ItemStatus.HANDLED.value if outcome == "approved" else ItemStatus.DISMISSED.value
        updated = store.update_status(item.id, status, owner=owner)
        if updated is None:
            return False
        updated.refs["auto_denied_outcome"] = outcome
        updated.message = (
            "The recorded call was retried and approved."
            if outcome == "approved"
            else "The recorded call was retried and explicitly rejected."
        )
        store.save()
        state.broadcast_ws("inbox_item_updated", _owner_item(updated, owner))
        return True
    except Exception:
        logger.debug("could not settle answered auto-denial row", exc_info=True)
        return False
