"""Shared TYPE_CHECKING imports for dashboard modules."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.context import ContextBuilder
    from gideon.history import ConversationLog, HistoryConsolidator
    from gideon.session import SessionManager
    from gideon.subagent import SubagentManager

__all__ = [
    "ContextBuilder",
    "ConversationLog",
    "HistoryConsolidator",
    "SessionManager",
    "SubagentManager",
]
