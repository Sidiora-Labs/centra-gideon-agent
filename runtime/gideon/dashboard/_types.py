"""Shared TYPE_CHECKING imports for dashboard modules."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.context import ContextBuilder
    from gideon.history import ConversationLog, HistoryConsolidator
    from gideon.learn import LessonStore
    from gideon.schedule import ScheduleService
    from gideon.session import SessionManager
    from gideon.subagent import SubagentManager

__all__ = [
    "ContextBuilder",
    "ScheduleService",
    "ConversationLog",
    "HistoryConsolidator",
    "LessonStore",
    "SessionManager",
    "SubagentManager",
]
