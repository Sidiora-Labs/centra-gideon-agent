"""SDK: the embedding-provider ABC + data types.

Stable re-export of ``gideon.integrations.embedding_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from gideon.integrations.embedding_providers.base import (
    EmbeddingModel,
    EmbeddingProvider,
)

__all__ = ["EmbeddingProvider", "EmbeddingModel"]
