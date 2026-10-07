"""Exactly-once answers for questions asked by an attended owner chat."""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
import uuid
import weakref
from dataclasses import dataclass

from gideon.assurance.validation import validate_ask_user_question
from gideon.security.approval_answer import OWNER
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.session_credentials import current_work

_CALL = contextvars.ContextVar("gideon_owner_question_call", default="")
_REGISTRY = None


def bind_call(call_id):
    return _CALL.set(str(call_id or ""))


def reset_call(token):
    _CALL.reset(token)


def install(registry):
    global _REGISTRY
    _REGISTRY = weakref.ref(registry)


def registry():
    return _REGISTRY() if _REGISTRY is not None else None


def redact(value):
    value, _ = redact_exfiltration_urls(str(value))
    value, _ = redact_credentials(value)
    return value


class QuestionRefused(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class Asked:
    id: str
    session: str
    call_id: str
    principal: object
    questions: list
    future: asyncio.Future
    deadline: float
    outcome: str = "pending"


class OwnerQuestions:
    def __init__(self, state):
        self.state = state
        self.pending = {}
        self.ended = {}
        self.window = 600.0

    def _admit(self, key):
        work = current_work()
        session = self.state.get_session(key.removeprefix("dashboard:"))
        if (
            work is None
            or work.session_key != key
            or not key.startswith("dashboard:")
            or work.initiator.kind != OWNER
            or work.created_by_app
            or (work.work_actor is not None and work.work_actor != work.initiator)
            or session is None
            or session.created_by_app
            or not session.running
        ):
            raise QuestionRefused(
                "question_unattended",
                "Ask in an attended owner chat; this work cannot wait for an owner answer.",
            )
        principal = session._initiator
        if principal != {
            "kind": work.initiator.kind,
            "name": work.initiator.name,
            "tenant": work.initiator.tenant,
        }:
            raise QuestionRefused(
                "question_owner_changed",
                "The chat's original owner does not match this turn.",
            )
        return session, work.initiator

    @staticmethod
    def card(asked):
        questions = []
        for question in asked.questions:
            questions.append(
                {
                    **question,
                    "question": redact(question["question"]),
                    "header": redact(question["header"]),
                    "options": [
                        {
                            "label": redact(option["label"]),
                            "description": redact(option["description"]),
                        }
                        for option in question["options"]
                    ],
                }
            )
        return {
            "id": asked.id,
            "session": asked.session.removeprefix("dashboard:"),
            "tool_call_id": asked.call_id,
            "questions": questions,
            "answerable": True,
            "outcome": asked.outcome,
            "deadline": asked.deadline,
        }

    def _record(self, asked, payload):
        session = self.state.get_session(asked.session.removeprefix("dashboard:"))
        if session is None:
            return
        match = None
        for row in reversed(session.messages):
            if (row.get("meta") or {}).get("tool_call_id") == asked.call_id:
                match = row
                break
            try:
                from gideon.interfaces.dashboard.state import parse_cls_meta

                details = parse_cls_meta(row.get("cls") or "") or {}
            except (ValueError, TypeError):
                continue
            if (
                isinstance(details, dict)
                and details.get("tool_call_id") == asked.call_id
            ):
                match = row
                break
        if match is None:
            session.append(
                "tool", "Owner question", meta={"tool_call_id": asked.call_id}
            )
            match = session.messages[-1]
        match.setdefault("meta", {})["owner_question"] = payload
        from gideon.interfaces.dashboard.chat_persistence import save_session_to_history

        save_session_to_history(self.state, session, force=True)

    def _settle(self, asked, kind, *, answers=None, reason=""):
        if asked.outcome != "pending":
            return False
        payload = {
            **self.card(asked),
            "outcome": kind,
            "answerable": False,
            "reason": reason,
        }
        if answers is not None:
            payload["answers"] = [
                {"selected": selected, "other": redact(other)}
                for selected, other in answers
            ]
        self._record(asked, payload)
        asked.outcome = kind
        self.pending.pop(asked.id, None)
        self.ended[asked.id] = kind
        while len(self.ended) > 256:
            self.ended.pop(next(iter(self.ended)))
        self.state.broadcast_ws("question_resolved", payload, owner_only=True)
        try:
            from gideon.integrations.inbox import resolve_attention_items

            resolve_attention_items(self.state, {"question": asked.id})
        except Exception:
            self.state._log.debug("question attention settlement failed", exc_info=True)
        if not asked.future.done():
            asked.future.set_result((kind, answers, reason))
        return True

    async def ask(self, key, arguments):
        questions = validate_ask_user_question(arguments)
        kind, answers, reason = await self._ask_outcome(key, arguments)
        if kind == "answered":
            values = [
                {
                    "question": question["question"],
                    "selected": [
                        question["options"][index]["label"] for index in selected
                    ],
                    "other": other,
                }
                for question, (selected, other) in zip(questions, answers)
            ]
            return json.dumps({"outcome": kind, "answers": values})
        return json.dumps({"outcome": kind, "reason": reason})

    async def ask_outcome(self, key, arguments, *, call_id):
        """Host transport entry; return accepted indices without label reconstruction."""
        token = bind_call(call_id)
        try:
            return await self._ask_outcome(key, arguments)
        finally:
            reset_call(token)

    async def _ask_outcome(self, key, arguments):
        _, principal = self._admit(key)
        call_id = _CALL.get()
        if not call_id:
            raise QuestionRefused(
                "question_call_missing",
                "The runtime did not supply a trusted tool call identity.",
            )
        if any(
            asked.session == key and asked.call_id == call_id
            for asked in self.pending.values()
        ):
            raise QuestionRefused(
                "question_already_waiting",
                "This tool call already has an owner question.",
            )
        questions = validate_ask_user_question(arguments)
        asked = Asked(
            uuid.uuid4().hex,
            key,
            call_id,
            principal,
            questions,
            asyncio.get_running_loop().create_future(),
            time.time() + self.window,
        )
        self.pending[asked.id] = asked
        try:
            self._record(asked, self.card(asked))
        except Exception:
            self.pending.pop(asked.id, None)
            asked.future.cancel()
            raise
        self.state.broadcast_ws("question_card", self.card(asked), owner_only=True)
        try:
            from gideon.integrations.inbox import ItemKind, emit_attention_item

            emit_attention_item(
                self.state,
                source="system",
                kind="needs_input",
                item_kind=ItemKind.AGENT_REQUEST.value,
                title="Your answer is needed",
                body=redact(questions[0]["question"]),
                refs={"question": asked.id, "session": key.removeprefix("dashboard:")},
                dedup_key="question:" + asked.id,
            )
        except Exception:
            self.state._log.debug(
                "question attention publication failed", exc_info=True
            )
        try:
            kind, answers, reason = await asyncio.wait_for(
                asyncio.shield(asked.future),
                timeout=max(0, asked.deadline - time.time()),
            )
            return kind, answers, reason
        except TimeoutError:
            self._settle(asked, "expired", reason="The original answer window expired.")
            return "expired", None, "The original answer window expired."
        except asyncio.CancelledError:
            self._settle(asked, "cancelled", reason="The requesting turn stopped.")
            raise

    def answer(self, question_id, key, principal, answers, skip=False):
        asked = self.pending.get(question_id)
        if asked is None:
            raise QuestionRefused(
                "question_ended" if question_id in self.ended else "question_not_found",
                "This question is no longer waiting for an answer.",
            )
        if (
            principal.kind != OWNER
            or principal != asked.principal
            or key != asked.session.removeprefix("dashboard:")
        ):
            raise QuestionRefused(
                "question_owner_required",
                "Only the original authenticated owner can answer this chat's question.",
            )
        if type(skip) is not bool:
            raise QuestionRefused("question_answer_invalid", "skip must be a boolean.")
        if time.time() >= asked.deadline:
            self._settle(asked, "expired", reason="The original answer window expired.")
            raise QuestionRefused(
                "question_ended", "This question's answer window expired."
            )
        session = self.state.get_session(key)
        if session is None or not session.running:
            self._settle(asked, "cancelled", reason="The requesting turn stopped.")
            raise QuestionRefused("question_ended", "The requesting turn stopped.")
        parsed = None
        if not skip:
            if not isinstance(answers, list) or len(answers) != len(asked.questions):
                raise QuestionRefused(
                    "question_answer_invalid",
                    "Send exactly one answer for each question.",
                )
            parsed = []
            for question, answer in zip(asked.questions, answers):
                if not isinstance(answer, dict):
                    raise QuestionRefused(
                        "question_answer_invalid", "Each answer must be an object."
                    )
                selected, other = answer.get("selected", []), answer.get("other", "")
                if (
                    not isinstance(selected, list)
                    or any(type(index) is not int for index in selected)
                    or len(set(selected)) != len(selected)
                    or any(
                        index < 0 or index >= len(question["options"])
                        for index in selected
                    )
                    or len(selected) > 1
                    and not question["multiSelect"]
                    or not isinstance(other, str)
                    or len(other) > 2000
                    or question.get("free_text", True) is False
                    and bool(other)
                    or not selected
                    and not other.strip()
                ):
                    raise QuestionRefused(
                        "question_answer_invalid",
                        "Choose valid offered options or up to 2000 characters of your own text.",
                    )
                parsed.append((selected, other))
        return self._settle(asked, "skipped" if skip else "answered", answers=parsed)

    def end_turn(self, key):
        key = key if key.startswith("dashboard:") else "dashboard:" + key
        for asked in list(self.pending.values()):
            if asked.session == key:
                self._settle(asked, "cancelled", reason="The requesting turn stopped.")

    def pending_for(self, key):
        key = key if key.startswith("dashboard:") else "dashboard:" + key
        return [
            self.card(asked) for asked in self.pending.values() if asked.session == key
        ]

    def expire_orphans(self, session):
        changed = False
        for row in session.messages:
            payload = (row.get("meta") or {}).get("owner_question")
            if (
                isinstance(payload, dict)
                and payload.get("outcome") == "pending"
                and payload.get("id") not in self.pending
            ):
                payload.update(
                    outcome="cancelled",
                    answerable=False,
                    reason="The requesting process ended; no answer channel survived.",
                )
                changed = True
                self.state.broadcast_ws("question_resolved", payload, owner_only=True)
                try:
                    from gideon.integrations.inbox import resolve_attention_items

                    resolve_attention_items(self.state, {"question": payload["id"]})
                except Exception:
                    self.state._log.debug(
                        "orphan question attention settlement failed", exc_info=True
                    )
        if changed:
            session._dirty = True
            from gideon.interfaces.dashboard.chat_persistence import (
                save_session_to_history,
            )

            save_session_to_history(self.state, session, force=True)
