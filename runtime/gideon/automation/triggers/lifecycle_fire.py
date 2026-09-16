"""Build bounded lifecycle context and deliver observational hooks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
DORMANT_EVENTS: tuple[str, ...] = (
    "PreResponse",
    "PostResponse",
    "SessionEnd",
    "MemoryWrite",
    "ContextCompact",
    "SubagentSpawn",
    "ApprovalRequest",
)
FIELD_CAP = 120
_CONTEXT_FIELDS = {
    "PreResponse": (("session", "session_key"), ("agent", "agent")),
    "PostResponse": (
        ("session", "session_key"),
        ("agent", "agent"),
        ("reply_chars", "reply_chars"),
        ("tool_calls", "tool_calls"),
    ),
    "SessionEnd": (
        ("session", "session_key"),
        ("reason", "reason"),
        ("turns", "turns"),
    ),
    "MemoryWrite": (
        ("kind", "kind"),
        ("key", "key"),
        ("scope", "scope"),
        ("session", "session_key"),
    ),
    "ContextCompact": (
        ("session", "session_key"),
        ("before", "before_chars"),
        ("after", "after_chars"),
    ),
    "SubagentSpawn": (
        ("subagent_id", "subagent_id"),
        ("parent_session_key", "parent_session_key"),
        ("agent_role", "agent_role"),
        ("depth", "depth"),
    ),
    "ApprovalRequest": (
        ("tool", "tool"),
        ("source", "source"),
        ("session", "session_key"),
        ("approval", "approval_id"),
    ),
}


def _kv(pairs: list[tuple[str, Any]]) -> str:
    normalized = (
        (key, str(value or "").strip().replace("\n", " ")) for key, value in pairs
    )
    return " ".join(f"{key}={text[:FIELD_CAP]}" for key, text in normalized if text)


def _payload(event: str, fields: dict[str, Any]) -> dict[str, str]:
    context = [(name, fields[argument]) for name, argument in _CONTEXT_FIELDS[event]]
    return dict(event=event, context=_kv(context))


def pre_response_payload(*, session_key: str = "", agent: str = "") -> dict[str, str]:
    return _payload("PreResponse", locals())


def post_response_payload(
    *, session_key: str = "", agent: str = "", reply_chars: int = 0, tool_calls: int = 0
) -> dict[str, str]:
    return _payload("PostResponse", locals())


def session_end_payload(
    *, session_key: str = "", reason: str = "", turns: int = 0
) -> dict[str, str]:
    return _payload("SessionEnd", locals())


def memory_write_payload(
    *, kind: str = "", key: str = "", scope: str = "", session_key: str = ""
) -> dict[str, str]:
    return _payload("MemoryWrite", locals())


def context_compact_payload(
    *, session_key: str = "", before_chars: int = 0, after_chars: int = 0
) -> dict[str, str]:
    return _payload("ContextCompact", locals())


def subagent_spawn_payload(
    *,
    subagent_id: str = "",
    parent_session_key: str = "",
    agent_role: str = "",
    depth: int = 0,
) -> dict[str, str]:
    return _payload("SubagentSpawn", locals())


def approval_request_payload(
    *, tool: str = "", source: str = "", session_key: str = "", approval_id: str = ""
) -> dict[str, str]:
    return _payload("ApprovalRequest", locals())


BUILDERS = {
    "PreResponse": pre_response_payload,
    "PostResponse": post_response_payload,
    "SessionEnd": session_end_payload,
    "MemoryWrite": memory_write_payload,
    "ContextCompact": context_compact_payload,
    "SubagentSpawn": subagent_spawn_payload,
    "ApprovalRequest": approval_request_payload,
}


@dataclass(frozen=True)
class HookObservation:
    payload: dict[str, str]
    extra: dict[str, Any]

    async def deliver(self) -> None:
        try:
            from gideon.engine.hooks import get_global_hook_store

            store = get_global_hook_store()
            if store is not None:
                await store.fire(
                    self.payload["event"],
                    context=self.payload.get("context", ""),
                    **self.extra,
                )
        except Exception:
            logger.debug(
                "lifecycle hook fire failed for %s",
                self.payload.get("event"),
                exc_info=True,
            )


def fire_sync(payload: dict[str, str], **extra: Any) -> None:
    try:
        active_loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        active_loop.create_task(fire(payload, **extra))
    except Exception:
        logger.debug(
            "lifecycle hook scheduling failed for %s",
            payload.get("event"),
            exc_info=True,
        )


async def fire(payload: dict[str, str], **extra: Any) -> None:
    await HookObservation(payload, extra).deliver()
