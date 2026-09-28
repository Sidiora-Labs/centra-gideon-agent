"""One dashboard registry and outcome path for pending owner decisions."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

APPROVAL_OUTCOMES = frozenset({"approved", "rejected", "expired", "cancelled"})
UNANSWERED_OUTCOMES = frozenset({"expired", "cancelled"})


def chat_approval_id(session: str, request_id: str) -> str:
    return f"{session}:{request_id}"


class DashboardApprovalState:
    """Mixin for ConsoleState; owns approval registration, expiry, withdrawal and answer."""

    def _hold_approval(self, entry: dict[str, Any], *, broadcast: bool = True) -> None:
        self._pending_approvals[entry["id"]] = entry
        if broadcast:
            self.broadcast_ws("approval", entry)
        self._push_approval(entry["id"])
        self._raise_approval_row(entry)

    def _register_chat_approval(self, data: dict[str, Any]) -> None:
        session = str(data.get("session") or "")
        request_id = str(data.get("id") or "")
        if not session or not request_id:
            return
        approval_id = chat_approval_id(session, request_id)
        if approval_id in self._pending_approvals:
            return
        session_obj = self._sessions.get(session)
        app_name = str(
            getattr(session_obj, "_created_by_app", "")
            or getattr(session_obj, "created_by_app", "")
            or ""
        )
        entry = {
            **data,
            "id": approval_id,
            "request_id": request_id,
            "session": session,
            "revision": uuid.uuid4().hex,
            "source": str(data.get("source") or "chat"),
            "asked_by": self.approval_asked_by(request_id, session_obj),
            "created_by_app": app_name,
            "ts": float(data.get("ts") or time.time()),
        }
        self._hold_approval(entry, broadcast=False)
        data["approval_id"] = approval_id
        data["revision"] = entry["revision"]

    def _raise_approval_row(self, entry: dict[str, Any]) -> None:
        try:
            from gideon.integrations.inbox import ItemKind, emit_attention_item

            approval_id = str(entry["id"])
            session = str(entry.get("session") or "")
            tool = str(entry.get("tool") or "a tool")
            emit_attention_item(
                self,
                source="system",
                kind="agent_request",
                item_kind=ItemKind.AGENT_REQUEST.value,
                title=f"Approval needed: {tool}",
                body=f"Work is waiting for your decision before running {tool}.",
                refs={"approval": approval_id, **({"session": session} if session else {})},
                dedup_key=f"approval:{approval_id}",
            )
        except Exception:
            self._log.debug("approval Inbox row failed", exc_info=True)

    def _push_approval(self, approval_id: str) -> None:
        try:
            from gideon.workspace import notification_rules, push

            rule = notification_rules.resolve_rule("approval", "requested")
            if rule.mode != "never" and "push" in rule.targets:
                push.deliver_async("approval", approval_id)
        except Exception:
            self._log.debug("approval push dispatch failed", exc_info=True)

    def _audit_and_broadcast_approval(
        self, session: str, approval_id: str, outcome: str, *,
        decided_by: str = "", entry: dict[str, Any] | None = None,
    ) -> None:
        try:
            from gideon.security.sel import sel

            sel().log_tool_invocation(
                session_key=session or "state",
                tool_name="approval_decision",
                outcome=outcome,
                request_id=approval_id,
                source="paired_channel" if decided_by.startswith("channel:") else "dashboard",
                metadata={"decided_by": decided_by or ("unanswered" if outcome in UNANSWERED_OUTCOMES else "you")},
            )
        except Exception:
            self._log.warning("SEL audit failed for approval resolution", exc_info=True)
        try:
            entry = entry or self._pending_approvals.get(approval_id) or {}
            self.broadcast_ws(
                "approval_resolved",
                {
                    "id": approval_id,
                    "request_id": str(entry.get("request_id") or approval_id),
                    "session": str(entry.get("session") or session),
                    "approved": outcome == "approved",
                    "outcome": outcome,
                    **({"reason": str(entry["owner_reason"])} if entry.get("owner_reason") else {}),
                },
            )
        except Exception:
            self._log.warning("WS broadcast failed for approval resolution", exc_info=True)

    def withdraw_approval(self, approval_id: str, *, outcome: str, decided_by: str = "") -> bool:
        if outcome not in APPROVAL_OUTCOMES:
            raise ValueError(f"unknown approval outcome {outcome!r}")
        entry = self._pending_approvals.pop(approval_id, None)
        if entry is None:
            return False
        self._approval_endings[approval_id] = outcome
        if len(self._approval_endings) > 512:
            self._approval_endings.pop(next(iter(self._approval_endings)))
        try:
            from gideon.integrations.inbox import resolve_attention_items

            resolve_attention_items(self, {"approval": approval_id})
        except Exception:
            self._log.debug("could not close approval Inbox row", exc_info=True)
        if outcome in UNANSWERED_OUTCOMES:
            try:
                from gideon.security.sel import sel

                sel().log_api_access(
                    caller=f"approval_owner:{entry.get('session') or entry.get('source') or 'unknown'}",
                    operation="approval_cancelled" if outcome == "cancelled" else "approval_expired",
                    outcome=outcome,
                    resources=f"{approval_id}: {entry.get('owner_reason') or 'no answer'}"[:300],
                )
            except Exception:
                self._log.debug("approval ending audit failed", exc_info=True)
        self._audit_and_broadcast_approval(
            str(entry.get("session") or ""), approval_id, outcome,
            decided_by=decided_by, entry=entry,
        )
        return True

    def ended_as(self, approval_id: str) -> str:
        return str(self._approval_endings.get(approval_id, ""))

    def end_approval(self, approval_id: str, *, outcome: str) -> None:
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return
        if outcome not in UNANSWERED_OUTCOMES:
            raise ValueError("end_approval only accepts expired or cancelled")
        fut = self._approval_futures.get(approval_id)
        if fut is not None and not fut.done():
            fut.set_result(False)
        request_id = str(entry.get("request_id") or "")
        session = self._sessions.get(str(entry.get("session") or ""))
        if request_id and session is not None:
            chat_future = session._approval_futures.get(request_id)
            if chat_future is not None and not chat_future.done():
                chat_future.set_result("expired" if outcome == "expired" else "cancelled")
            from gideon.interfaces.dashboard.state import _mark_permission_resolved

            _mark_permission_resolved(session.messages, request_id, outcome)
        self.withdraw_approval(approval_id, outcome=outcome)

    def cancel_approval(self, approval_id: str, *, reason: str) -> bool:
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return False
        entry["owner_reason"] = reason
        self.end_approval(approval_id, outcome="cancelled")
        return True

    def cancel_approvals(self, *, session_prefix: str, reason: str) -> int:
        if not session_prefix:
            return 0
        ids = [
            aid for aid, entry in self._pending_approvals.items()
            if str(entry.get("session") or "").startswith(session_prefix)
        ]
        return sum(self.cancel_approval(aid, reason=reason) for aid in ids)

    def cancel_turn_approvals(self, session_key: str) -> int:
        key = session_key.removeprefix("dashboard:")
        session = self._sessions.get(key)
        if session is None:
            return 0
        ids = [
            chat_approval_id(session.key, str(request_id))
            for request_id, future in session._approval_futures.items()
            if not future.done()
        ]
        return sum(self.cancel_approval(aid, reason="its chat turn was stopped") for aid in ids)

    def loop_status_changed(self, loop_id: str, _old: Any, new: Any) -> None:
        status = str(getattr(new, "value", new))
        reason = f"the loop that requested approval ended as {status}"
        for prefix in (f"loop-{loop_id}", f"loop-plan-{loop_id}", f"code-plan-{loop_id}"):
            self.cancel_approvals(session_prefix=prefix, reason=reason)

    def cancel_subagent_approvals(self, agent_id: str, *, reason: str) -> int:
        prefixes = (f"spawn:{agent_id}", f"subagent:{agent_id}:")
        ids = [
            approval_id
            for approval_id, entry in self._pending_approvals.items()
            if approval_id.startswith(prefixes)
            or str(entry.get("source") or "").startswith(prefixes)
        ]
        return sum(self.cancel_approval(approval_id, reason=reason) for approval_id in ids)

    def refuse_ended_owner(self, approval_id: str) -> str:
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return ""
        from gideon.interfaces.dashboard.approval_owner import owner_ended

        reason = owner_ended(entry, state=self)
        if reason:
            entry["owner_reason"] = reason
            self.cancel_approval(approval_id, reason=reason)
        return reason

    def approval_asked_by(self, approval_id: str, session: Any = None) -> str:
        pending = self._pending_approvals.get(approval_id)
        if pending:
            return str(pending.get("asked_by") or "")
        request_id = approval_id.rsplit(":", 1)[-1]
        sessions = [session] if session is not None else self._sessions.values()
        for current in sessions:
            for message in reversed(current.messages):
                if message.get("role") != "permission":
                    continue
                try:
                    meta = json.loads(message.get("cls", "{}") or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if str(meta.get("request_id") or "") == request_id:
                    from gideon.security.approval_answer import asker_of_chat

                    return str(meta.get("asked_by") or asker_of_chat(current.key, created_by_app=str(getattr(current, "_created_by_app", "") or "")).label)
        return ""

    async def request_approval(
        self, approval_id: str, source: str, tool: str, *, tool_input: str = "",
        tool_purpose: str = "", session: str = "", asked_by: str = "",
    ) -> bool:
        current = self._approval_futures.get(approval_id)
        if current is not None and not current.done():
            return False
        fut = asyncio.get_running_loop().create_future()
        self._approval_futures[approval_id] = fut
        safe_tool, _ = self._redact_approval_text(tool)
        safe_input, _ = self._redact_approval_text(tool_input)
        safe_purpose, _ = self._redact_approval_text(tool_purpose)
        entry = {
            "id": approval_id, "revision": uuid.uuid4().hex, "source": source,
            "tool": safe_tool, "tool_input": safe_input, "tool_purpose": safe_purpose,
            "session": session, "asked_by": asked_by or self._approval_requester(source, session),
            "ts": time.time(),
        }
        self._hold_approval(entry)
        from gideon.automation.triggers.lifecycle_fire import approval_request_payload
        from gideon.automation.triggers.lifecycle_fire import fire

        try:
            await fire(
                approval_request_payload(tool=safe_tool, source=source, session_key=session, approval_id=approval_id),
                tool_name=safe_tool,
            )
            from gideon.security.approval_grants import approval_window_secs

            timeout = min(self._approval_timeout_for(source), approval_window_secs(self._approval_timeout_for(source)))
            try:
                return bool(await asyncio.wait_for(fut, timeout=timeout))
            except asyncio.TimeoutError:
                self.end_approval(approval_id, outcome="expired")
                return False
        except asyncio.CancelledError:
            self.end_approval(approval_id, outcome="cancelled")
            raise
        finally:
            if approval_id in self._pending_approvals:
                self.end_approval(approval_id, outcome="cancelled")
            if self._approval_futures.get(approval_id) is fut:
                self._approval_futures.pop(approval_id, None)

    @staticmethod
    def _redact_approval_text(value: str) -> tuple[str, int]:
        from gideon.security.security import redact_credentials, redact_exfiltration_urls

        value, count = redact_exfiltration_urls(value)
        value, credential_count = redact_credentials(value)
        return value, count + credential_count

    def _approval_timeout_for(self, source: str) -> float:
        low = (source or "").lower()
        if any(marker in low for marker in self._UNATTENDED_SOURCE_MARKERS):
            return self._UNATTENDED_APPROVAL_TIMEOUT
        return self._APPROVAL_TIMEOUT

    @staticmethod
    def _approval_requester(source: str, session: str) -> str:
        from gideon.security.approval_answer import agent, run, trigger

        if session:
            return agent(session.removeprefix("dashboard:")).label
        lowered = (source or "").lower()
        if lowered.startswith("trigger:"):
            return trigger(lowered.partition(":")[2]).label
        if lowered.startswith(("workflow:", "run:")):
            return run(lowered.partition(":")[2]).label
        return agent(source or "unknown").label

    def resolve_session_approval(self, session: Any, approval_id: str, response: str, *, by: Any) -> bool:
        from gideon.security.approval_answer import OWNER, UNKNOWN, Principal, check

        principal = by if isinstance(by, Principal) else Principal(UNKNOWN)
        registry_id = chat_approval_id(session.key, approval_id)
        entry = self._pending_approvals.get(registry_id)
        asked_by = self.approval_asked_by(registry_id, session)
        if check(principal, what=f"approval:{registry_id}", asked_by=asked_by):
            return False
        reason = self.refuse_ended_owner(registry_id)
        if reason:
            return False
        future = session._approval_futures.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(response)
        from gideon.interfaces.dashboard.state import _mark_permission_resolved

        _mark_permission_resolved(session.messages, approval_id, response)
        outcome = "approved" if response.startswith("approved") else "rejected"
        decided_by = "you" if principal.kind == OWNER else principal.label
        if entry is not None:
            self.withdraw_approval(registry_id, outcome=outcome, decided_by=decided_by)
        self.push_sessions_update()
        return True

    def resolve_approval(self, approval_id: str, approved: bool, *, by: Any = None) -> bool:
        from gideon.security.approval_answer import OWNER, UNKNOWN, Principal, check

        principal = by if isinstance(by, Principal) else Principal(UNKNOWN)
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return False
        if check(principal, what=f"approval:{approval_id}", asked_by=str(entry.get("asked_by") or "")):
            return False
        if self.refuse_ended_owner(approval_id):
            return False
        request_id = str(entry.get("request_id") or "")
        session = self._sessions.get(str(entry.get("session") or ""))
        if request_id and session is not None:
            return self.resolve_session_approval(session, request_id, "approved" if approved else "rejected", by=principal)
        fut = self._approval_futures.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        decided_by = "you" if principal.kind == OWNER else principal.label
        self.withdraw_approval(
            approval_id,
            outcome="approved" if approved else "rejected",
            decided_by=decided_by,
        )
        return True

    def resolve_approval_revision(self, approval_id: str, approved: bool, expected_revision: str, *, by: Any = None) -> str:
        pending = self._pending_approvals.get(approval_id)
        if pending is None:
            ended = self.ended_as(approval_id)
            if ended == "cancelled":
                return "owner_ended"
            if ended == "expired":
                return "expired"
            return "missing"
        if pending.get("revision") != expected_revision:
            return "revision_conflict"
        if self.refuse_ended_owner(approval_id):
            return "owner_ended"
        if self.resolve_approval(approval_id, approved, by=by):
            return "resolved"
        ended = self.ended_as(approval_id)
        return "owner_ended" if ended == "cancelled" else (
            "expired" if ended == "expired" else "missing"
        )
