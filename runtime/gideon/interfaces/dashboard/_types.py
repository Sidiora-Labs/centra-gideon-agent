"""Shared TYPE_CHECKING imports for dashboard modules."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.cognition.context import PromptAssembler
    from gideon.cognition.history import ConversationLog, HistoryConsolidator
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.subagent import DelegationSupervisor

__all__ = [
    "PromptAssembler",
    "ConversationLog",
    "HistoryConsolidator",
    "ConversationDirectory",
    "DelegationSupervisor",
]
