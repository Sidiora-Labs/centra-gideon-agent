"""Per-turn decoding state for ACP session event streams."""

from __future__ import annotations

from gideon.core.constants import JSONRPC_METHOD_NOT_FOUND
from gideon.integrations.acp import translate
from gideon.integrations.acp.errors import AcpError, AcpMethodNotFound
from gideon.integrations.acp.types import (
    EVENT_AGENT_SWITCHED,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    AcpEvent,
)


class TurnDecoder:
    def __init__(self, session, *, method: str, command: bool):
        self.session = session
        self.method = method
        self.command = command
        self.finished = False
        self.stale_eligible = False
        self.agent_announced = False
        self.tool_boundary = False
        self.handlers = {
            "complete": self.complete,
            "error": self.error,
            "permission": self.permission,
            "update": self.update,
            "metadata": self.metadata,
            "compaction": self.compaction,
            "clear": lambda msg: [AcpEvent(kind=EVENT_CLEAR_STATUS)],
            "agent_switched": self.agent_switched,
        }

    def accept(self, action: str, message) -> list[AcpEvent]:
        self.session.last_prompt_stats.event_count += 1
        self.stale_eligible = self.tool_boundary = False
        handler = self.handlers.get(action)
        return handler(message) if handler is not None else []

    def finish(self, reason: str = "") -> AcpEvent:
        self.finished = True
        return AcpEvent(kind=EVENT_COMPLETE, stop_reason=reason)

    def complete(self, message) -> list[AcpEvent]:
        result = message.result if isinstance(message.result, dict) else {}
        events = []
        if self.command:
            text = translate.format_command_result(result)
            if text:
                events.append(AcpEvent(kind=EVENT_TEXT_CHUNK, text=text))
            details = result.get("data")
            agent = details.get("agent") if isinstance(details, dict) else None
            if (
                not self.agent_announced
                and isinstance(agent, dict)
                and agent.get("name")
            ):
                events.append(AcpEvent(kind=EVENT_AGENT_SWITCHED, text=agent["name"]))
        events.extend(self.session._read_new_tool_results())
        events.append(self.finish(result.get("stopReason") or ""))
        return events

    def error(self, message) -> list[AcpEvent]:
        payload = message.error if isinstance(message.error, dict) else {}
        if payload.get("code") == JSONRPC_METHOD_NOT_FOUND:
            raise AcpMethodNotFound(self.method, message.error)
        raise AcpError(f"Prompt error: {message.error}")

    def permission(self, message) -> list[AcpEvent]:
        owner = self.session
        return [
            translate.build_permission_event(
                message,
                owner._dialect,
                owner._tool_call_inputs,
                owner._tool_call_seen,
                owner._offered_options,
            )
        ]

    def update(self, message) -> list[AcpEvent]:
        owner = self.session
        events = []
        text, thinking = translate.extract_text_chunk(message)
        if text:
            events.extend(owner._read_new_tool_results())
            events.append(
                AcpEvent(
                    kind=EVENT_THINKING_CHUNK if thinking else EVENT_TEXT_CHUNK,
                    text=text,
                )
            )
            if not thinking:
                owner.last_prompt_stats.text_chunks += 1
                self.stale_eligible = True
                if translate.is_tool_interrupted_marker(text):
                    events.extend(owner._read_new_tool_results())
                    events.append(self.finish())
                    return events
        tool = translate.extract_tool_event(
            message,
            owner._tool_call_inputs,
            owner._tool_call_seen,
            owner.last_prompt_stats.tool_calls,
        )
        if tool is not None:
            events.extend(owner._read_new_tool_results())
            events.append(tool)
        changes = translate.extract_tool_update_events(
            message, owner._tool_call_inputs, owner._tool_call_seen
        )
        events.extend(changes)
        self.tool_boundary = bool(tool or changes)
        return events

    def metadata(self, message) -> list[AcpEvent]:
        percent = translate.extract_context_pct(message)
        if percent is not None:
            self.session.last_prompt_stats.context_pct = percent
        return []

    def compaction(self, message) -> list[AcpEvent]:
        params = message.params or {}
        status = params.get("status", {})
        name = status.get("type", "") if isinstance(status, dict) else str(status)
        return [
            AcpEvent(
                kind=EVENT_COMPACTION_STATUS, text=name, title=params.get("summary", "")
            )
        ]

    def agent_switched(self, message) -> list[AcpEvent]:
        self.agent_announced = True
        return [
            AcpEvent(
                kind=EVENT_AGENT_SWITCHED,
                text=(message.params or {}).get("agentName", ""),
            )
        ]
