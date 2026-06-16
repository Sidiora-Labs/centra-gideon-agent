"""SDK: the knowledge-provider ABC + data types.

Stable re-export of ``gideon.knowledge_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.

Includes the poll contract (WATCHED-SOURCES §1.1): an app that watches an external
feed subclasses :class:`KnowledgeSourceProvider` and returns
:class:`SourcePollResult` of :class:`SourceItem` from ``poll``.
"""

from gideon.knowledge_providers.base import (  # noqa: F401
    KnowledgeItem,
    KnowledgeProvider,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)

__all__ = [
    "KnowledgeProvider",
    "KnowledgeSource",
    "KnowledgeItem",
    "KnowledgeSourceProvider",
    "SourceItem",
    "SourcePollResult",
]
