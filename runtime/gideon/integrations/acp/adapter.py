"""Project ACP event records onto the runtime's neutral event schema."""

from dataclasses import fields

from gideon.integrations.acp.types import AcpEvent
from gideon.integrations.llm.events import AgentEvent

_NEUTRAL_FIELDS = frozenset(field.name for field in fields(AgentEvent))
_TRANSFER_FIELDS = tuple(
    field.name for field in fields(AcpEvent) if field.name in _NEUTRAL_FIELDS
)


def acp_event_to_agent_event(e: AcpEvent) -> AgentEvent:
    return AgentEvent(**{name: getattr(e, name) for name in _TRANSFER_FIELDS})
