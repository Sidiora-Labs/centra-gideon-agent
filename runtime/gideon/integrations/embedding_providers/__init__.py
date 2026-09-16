"""Embedding providers — pluggable text embedding backends."""

from gideon.integrations.embedding_providers.base import (
    EmbeddingModel,
    EmbeddingProvider,
)

__all__ = ["EmbeddingModel", "EmbeddingProvider"]
