"""SDK: the knowledge-provider ABC + data types.

Stable re-export of ``gideon.knowledge_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from gideon.knowledge_providers.base import (  # noqa: F401
    KnowledgeItem,
    KnowledgeProvider,
    KnowledgeSource,
)

__all__ = ["KnowledgeProvider", "KnowledgeSource", "KnowledgeItem"]
