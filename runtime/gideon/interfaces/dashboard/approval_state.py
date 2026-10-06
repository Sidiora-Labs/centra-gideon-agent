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
        call_fingerprint = str(data.pop("_call_fingerprint", "") or "")
        retry_note_id = str(data.pop("_auto_denied_note_id", "") or "")
        retry_origin_kind = str(data.pop("_auto_denied_origin_kind", "") or "")
        retry_origin_id = str(data.pop("_auto_denied_origin_id", "") or "")
        retry_attempt_id = str(data.pop("_auto_denied_attempt_id", "") or "")
        retry_node_id = str(data.pop("_auto_denied_node_id", "") or "")
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
            "_call_fingerprint": call_fingerprint,
            "_auto_denied_note_id": retry_note_id,
            "_auto_denied_origin_kind": retry_origin_kind,
            "_auto_denied_origin_id": retry_origin_id,
            "_auto_denied_attempt_id": retry_attempt_id,
            "_auto_denied_node_id": retry_node_id,
        }
        self._hold_approval(entry, broadcast=False)
        data["approval_id"] = approval_id
        data["revision"] = entry["revision"]
        self._ask_origin_channel(entry)

    def _channel_context(self, entry: dict) -> dict | None:
        from gideon.interfaces.dashboard.chat_utils import _history_key_for
        from gideon.integrations.channel_transports import get_transport
        from gideon.integrations.channel_trust import is_allowed_sender
        from gideon.security.approval_answer import on_channel
        from gideon.security.session_credentials import credential_for, verify

        key = str(entry.get("session") or "")
        provider = self.channel_provider_for(key)
        if not key or provider not in {"telegram", "slack"}:
            return None
        history_key = _history_key_for(key)
        thread, channel = self.sessions.get_channel_link(history_key)
        if provider == "slack":
            from gideon.integrations.channel_delivery import raw_delivery_for
            delivery = raw_delivery_for("slack")
            identity = delivery.approval_identity(str(channel)) if delivery is not None and hasattr(delivery, "approval_identity") else None
            if identity is None or not thread or not channel:
                return None
            return {"provider": "slack", "thread": str(thread), "channel": str(channel),
                    **identity, "principal": on_channel("slack", identity["owner"], identity["tenant"]),
                    "work": verify(credential_for(history_key), history_key)}
        pieces = str(thread).split(":")
        if not pieces or pieces[0] != "telegram":
            return None
        slot = pieces[1] if len(pieces) == 4 else "primary"
        transport = get_transport("telegram")
        child = (getattr(transport, "bots", {}) or {}).get(slot)
        if child is None and getattr(transport, "slot", None) == slot:
            child = transport
        if child is None or not getattr(child, "connected", False):
            return None
        owner = str(child.config.get("owner_id") or "")
        if not owner or not is_allowed_sender("telegram", owner):
            return None
        return {"provider": "telegram", "thread": str(thread), "channel": str(channel),
                "owner": owner, "tenant": f"telegram:{slot}", "transport": child, "private": str(channel) == owner,
                "principal": on_channel("telegram", owner, f"telegram:{slot}"),
                "work": verify(credential_for(history_key), history_key)}

    def channel_answers(self, entry: dict, *, context: dict | None) -> tuple:
        from gideon.integrations.channel_delivery import APPROVAL_ANSWERS, ONE_CALL_ANSWERS
        from gideon.security.approval_answer import OWNER, CHANNEL, APP
        from gideon.security.approval_grants import stands, TRUST
        from gideon.security.guardrails.ladder import approval_screening_verdict

        if context is None:
            return ONE_CALL_ANSWERS
        session = self._sessions.get(str(entry.get("session") or ""))
        work = context.get("work")
        own = session is not None and entry.get("id") == chat_approval_id(session.key, str(entry.get("request_id") or ""))
        eligible_work = work is not None and not work.created_by_app and (work.work_actor is None or work.work_actor.kind != APP)
        if eligible_work:
            eligible_work = work.initiator.kind == OWNER or (
                work.initiator.kind == CHANNEL and work.initiator == context["principal"])
        if (not own or not eligible_work or getattr(session, "created_by_app", "")
            or getattr(session, "_app", "") in {"loop", "loops", "room"}
            or not context.get("private")
            or entry.get("owner_only") or entry.get("protected_delete")
            or entry.get("risk") not in {"safe", "caution"}
            or (work is not None and work.durable_run_id)
            or not approval_screening_verdict(TRUST).allowed
            or not stands(TRUST, caller="channel", audit=False)):
            return ONE_CALL_ANSWERS
        return APPROVAL_ANSWERS

    def grant_chat_trust(self, session: Any, request_id: str, *, by: Any, channel_offer: dict | None = None) -> bool:
        from gideon.security.approval_answer import OWNER, CHANNEL, check
        from gideon.security.approval_grants import stands, TRUST
        registry_id = chat_approval_id(session.key, request_id)
        entry = self._pending_approvals.get(registry_id) or {}
        future = session._approval_futures.get(request_id)
        if future is None or future.done() or check(by, what=f"approval:{registry_id}", asked_by=self.approval_asked_by(registry_id, session)):
            return False
        if by.kind == CHANNEL:
            if channel_offer is None or self.__dict__.get("_channel_offers", {}).get(registry_id) is not channel_offer:
                return False
        elif by.kind != OWNER:
            return False
        if self.refuse_ended_owner(registry_id) or not stands(TRUST, caller=by.label, subject=f"session={session.key}"):
            return False
        session._trust = True
        session._agent_floor_seeded = False
        from gideon.interfaces.dashboard.chat_utils import _history_key_for
        self.sessions.set_approval_policy(_history_key_for(session.key), "auto")
        return True

    def answer_on_channel(self, approval_id: str, answer: str, *, by: Any) -> bool:
        from gideon.security.approval_answer import CHANNEL, Principal
        from gideon.integrations.channel_delivery import ALLOW_FOR_THIS_CHAT
        offer = self.__dict__.get("_channel_offers", {}).get(approval_id)
        entry = self._pending_approvals.get(approval_id)
        if offer is None or entry is None or entry.get("revision") != offer["revision"]:
            return False
        context = self._channel_context(entry)
        if context is None or not isinstance(by, Principal) or by.kind != CHANNEL or by != offer["context"]["principal"]:
            return False
        original = offer["context"]
        if any(context.get(k) != original.get(k) for k in ("provider", "thread", "channel", "owner", "tenant", "transport")):
            return False
        chosen = next((value for value in offer["answers"] if value.key == answer), None)
        if chosen is None:
            return False
        if chosen == ALLOW_FOR_THIS_CHAT:
            if context["work"] is not original["work"] or chosen not in self.channel_answers(entry, context=context):
                return False
            session = self._sessions.get(str(entry.get("session") or ""))
            request_id = str(entry.get("request_id") or "")
            if session is None or not self.grant_chat_trust(session, request_id, by=by, channel_offer=offer):
                return False
            if not self.resolve_session_approval(session, request_id, "approved", by=by):
                return False
            from gideon.interfaces.dashboard.state import _mark_permission_resolved
            _mark_permission_resolved(session.messages, request_id, "trust")
            self.push_sessions_update()
            return True
        return self.resolve_approval(approval_id, chosen.ends == "approved", by=by)

    def _ask_origin_channel(self, entry: dict) -> None:
        if not entry.get("request_id"):
            return
        context = self._channel_context(entry)
        if context is None:
            return
        tasks = self.__dict__.setdefault("_background_tasks", set())
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._approval_on_origin_channel(dict(entry), context))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def _approval_on_origin_channel(self, entry: dict, context: dict) -> None:
        import secrets
        from types import SimpleNamespace
        from gideon.integrations.channel_delivery import delivery_for
        from gideon.security.approval_brief import APPROVAL_BRIEF_META_KEY, entry_approval_brief
        approval_id = str(entry["id"])
        current = self._pending_approvals.get(approval_id)
        if current is None or current.get("revision") != entry.get("revision"):
            return
        delivery = delivery_for(context["provider"])
        if delivery is None:
            return
        if context["provider"] == "slack":
            await delivery.prepare_approval_channel(context["channel"])
            refreshed = self._channel_context(entry)
            if refreshed is None or any(refreshed.get(k) != context.get(k) for k in ("provider", "thread", "channel", "owner", "tenant", "transport", "work")):
                return
            context = refreshed
            current = self._pending_approvals.get(approval_id)
            if current is None or current.get("revision") != entry.get("revision"):
                return
        answers = self.channel_answers(entry, context=context)
        offer = {"revision": entry["revision"], "context": context, "answers": answers}
        offers = self.__dict__.setdefault("_channel_offers", {})
        offers[approval_id] = offer
        event = SimpleNamespace(request_id=secrets.token_hex(12), title=entry.get("tool", ""),
            tool_input=entry.get("tool_input", ""), tool_purpose=entry.get("tool_purpose", ""),
            risk_level=entry.get("risk", ""), tool_meta={APPROVAL_BRIEF_META_KEY: entry_approval_brief(entry, answers=answers)})
        delivery = delivery_for(context["provider"])
        if delivery is None:
            offers.pop(approval_id, None)
            return
        seen = {}
        def prompted(pending):
            if approval_id not in self._pending_approvals:
                if not pending.future.done():
                    pending.future.set_result(self.ended_as(approval_id) or "cancelled")
                return True
            seen["pending"] = pending
            pending.on_answer = lambda answer, by: self.answer_on_channel(approval_id, answer, by=by)
            self.__dict__.setdefault("_channel_prompts", {})[approval_id] = pending
            return True
        try:
            await delivery.request_approval(event, source=str(entry.get("source") or "chat"),
                parent_session_key=str(entry.get("session") or ""), sessions=self.sessions, on_prompted=prompted)
            pending = seen.get("pending")
            if pending is not None and pending.future.done() and not pending.future.cancelled():
                by = getattr(pending, "answerer", None)
                if by is not None:
                    self.answer_on_channel(approval_id, str(pending.future.result()), by=by)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log.debug("origin channel approval unavailable", exc_info=True)
        finally:
            self.__dict__.get("_channel_prompts", {}).pop(approval_id, None)
            if offers.get(approval_id) is offer:
                offers.pop(approval_id, None)

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
        pending = self.__dict__.get("_channel_prompts", {}).pop(approval_id, None)
        if pending is not None and not pending.future.done():
            pending.future.set_result(outcome)
        self.__dict__.get("_channel_offers", {}).pop(approval_id, None)
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
        if outcome == "expired":
            try:
                from gideon.interfaces.dashboard.auto_denials import record_auto_denial

                record_auto_denial(
                    self,
                    session=str(entry.get("session") or ""),
                    call_id=str(entry.get("request_id") or approval_id),
                    tool=str(entry.get("tool") or "a tool"),
                    fingerprint=str(entry.get("_call_fingerprint") or ""),
                    reason="expired",
                    source=str(entry.get("source") or ""),
                )
            except Exception:
                self._log.debug("expired approval denial row failed", exc_info=True)
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
        ids.extend(aid for aid, entry in self._pending_approvals.items()
                   if entry.get("source") == "workflow_batch" and entry.get("session") == key and aid not in ids)
        return sum(self.cancel_approval(aid, reason="its chat turn was stopped") for aid in ids)

    def loop_status_changed(self, loop_id: str, _old: Any, new: Any) -> None:
        status = str(getattr(new, "value", new))
        reason = f"the loop that requested approval ended as {status}"
        for prefix in (f"loop-{loop_id}", f"loop-plan-{loop_id}", f"code-plan-{loop_id}"):
            self.cancel_approvals(session_prefix=prefix, reason=reason)

    def rearm_loop_approval_posture(self, loop_id: str, *, attended: bool) -> int:
        """Withdraw pending owner prompts when a loop is re-armed unattended.

        A prompt created by an earlier attended run must not remain actionable after
        the persisted loop mode changes. The normal dashboard approval path remains
        the only way to answer prompts for currently attended workers.
        """
        if attended:
            return 0
        return sum(self.cancel_approvals(
            session_prefix=prefix,
            reason="the loop was re-armed without an attending owner",
        ) for prefix in (f"loop-{loop_id}", f"loop-plan-{loop_id}"))

    def waiting_on_owner(self, session_key: str) -> bool:
        key = session_key.removeprefix("dashboard:")
        session = self._sessions.get(key)
        if session is not None and any(not future.done() for future in session._approval_futures.values()):
            return True
        return any(
            entry.get("session", "").removeprefix("dashboard:") == key
            and (future := self._approval_futures.get(approval_id)) is not None
            and not future.done()
            for approval_id, entry in self._pending_approvals.items()
        )


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
        checked_entry, loop_reason = self._current_attended_loop_approval_entry(entry)
        if loop_reason:
            entry["owner_reason"] = loop_reason
            self.cancel_approval(approval_id, reason=loop_reason)
            return loop_reason
        from gideon.interfaces.dashboard.approval_owner import owner_ended

        reason = owner_ended(checked_entry, state=self)
        if reason:
            entry["owner_reason"] = reason
            self.cancel_approval(approval_id, reason=reason)
        return reason

    @staticmethod
    def _current_attended_loop_approval_entry(
        entry: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Map a main or parallel loop-worker approval to its persisted owner row.

        The owner-ending check understands the main ``loop-<id>`` key. Parallel
        workers append a task id, so resolve only that exact current-home worker
        prefix, require its persisted attended mode, and check loop liveness against
        the main key. Missing or unreadable rows fail closed.
        """
        session = str(entry.get("session") or "").removeprefix("dashboard:")
        if not session.startswith("loop-"):
            return entry, ""
        try:
            from gideon.automation.loop import manager, store
            from gideon.automation.loop.plan_walkthrough import planner_session_key

            for loop in store.list_all():
                main_key = manager.session_key(loop.id)
                if session != planner_session_key(loop.id) and session != main_key and not session.startswith(f"{main_key}-"):
                    continue
                if getattr(loop, "attended", None) is not True:
                    return {}, "the loop is no longer attended by its owner"
                checked = dict(entry)
                checked["session"] = main_key
                checked["request_id"] = ""
                return checked, ""
        except Exception:
            return {}, "the loop approval owner could not be verified"
        return {}, "the loop approval owner could not be verified"

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
        risk_level: object = "", tool_kind: str = "", tool_annotations: dict | None = None,
        owner_only: bool = False, on_decision: Any = None, approval_timeout_secs: float | None = None,
    ) -> bool:
        current = self._approval_futures.get(approval_id)
        if current is not None and not current.done():
            return False
        fut = asyncio.get_running_loop().create_future()
        self._approval_futures[approval_id] = fut
        from gideon.interfaces.dashboard.auto_denials import call_fingerprint

        from types import SimpleNamespace
        from gideon.security.approval_brief import compose_approval_brief
        from gideon.security.protected_folders import call_protected_delete, provider_working_folder, sentence
        event = SimpleNamespace(title=tool, tool_kind=tool_kind, tool_input=tool_input, tool_purpose=tool_purpose, risk_level=risk_level, tool_annotations=tool_annotations)
        brief = compose_approval_brief(event) or {}
        provider = self.sessions.get_provider(session) or self.sessions.get_provider(f"dashboard:{session}")
        protected = call_protected_delete(risk_level, tool, tool_kind, tool_input, cwd=provider_working_folder(provider))
        raw_call_fingerprint = call_fingerprint(tool, tool_input)
        safe_tool, _ = self._redact_approval_text(tool)
        safe_input, _ = self._redact_approval_text(tool_input)
        safe_purpose, _ = self._redact_approval_text(tool_purpose)
        entry = {
            "id": approval_id, "revision": uuid.uuid4().hex, "source": source,
            "owner_only": owner_only,
            "tool": safe_tool, "tool_input": safe_input, "tool_purpose": safe_purpose,
            "risk": brief.get("risk", "caution"), "blast_radius": brief.get("blastRadius"), "deny_consequence": brief.get("denyConsequence"), "protected_delete": sentence(protected),
            "session": session, "asked_by": asked_by or self._approval_requester(source, session),
            "ts": time.time(),
            "_call_fingerprint": raw_call_fingerprint,
        }
        if on_decision is not None:
            callbacks = getattr(self, "_approval_decision_callbacks", None)
            if callbacks is None:
                callbacks = self._approval_decision_callbacks = {}
            callbacks[approval_id] = on_decision
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
            if approval_timeout_secs is not None:
                timeout = min(timeout, max(0.0, approval_timeout_secs))
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
            getattr(self, "_approval_decision_callbacks", {}).pop(approval_id, None)
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
            note_id = str(entry.get("_auto_denied_note_id") or "")
            fingerprint = str(entry.get("_call_fingerprint") or "")
            if note_id and fingerprint:
                from gideon.interfaces.dashboard.auto_denials import settle_answered_call

                settle_answered_call(
                    self,
                    note_id=note_id,
                    session=session.key,
                    fingerprint=fingerprint,
                    outcome=outcome,
                    principal=principal,
                    origin_kind=str(entry.get("_auto_denied_origin_kind") or ""),
                    origin_id=str(entry.get("_auto_denied_origin_id") or ""),
                    attempt_id=str(entry.get("_auto_denied_attempt_id") or ""),
                    node_id=str(entry.get("_auto_denied_node_id") or ""),
                )
            self.withdraw_approval(registry_id, outcome=outcome, decided_by=decided_by)
        self.push_sessions_update()
        return True

    def resolve_approval(self, approval_id: str, approved: bool, *, by: Any = None) -> bool:
        from gideon.security.approval_answer import OWNER, UNKNOWN, Principal, check

        principal = by if isinstance(by, Principal) else Principal(UNKNOWN)
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return False
        if entry.get("owner_only") and principal.kind != OWNER:
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
        callback = getattr(self, "_approval_decision_callbacks", {}).get(approval_id)
        if callable(callback):
            callback(approved, principal)
        fut.set_result(approved)
        decided_by = "you" if principal.kind == OWNER else principal.label
        note_id = str(entry.get("_auto_denied_note_id") or "")
        fingerprint = str(entry.get("_call_fingerprint") or "")
        if note_id and fingerprint:
            from gideon.interfaces.dashboard.auto_denials import settle_answered_call

            settle_answered_call(
                self,
                note_id=note_id,
                session=str(entry.get("session") or ""),
                fingerprint=fingerprint,
                outcome="approved" if approved else "rejected",
                principal=principal,
                origin_kind=str(entry.get("_auto_denied_origin_kind") or ""),
                origin_id=str(entry.get("_auto_denied_origin_id") or ""),
                attempt_id=str(entry.get("_auto_denied_attempt_id") or ""),
                node_id=str(entry.get("_auto_denied_node_id") or ""),
            )
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
