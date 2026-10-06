"""Embedding inference through Gideon's configured provider authority."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Protocol

from gideon.integrations.embedding_providers import registry as gideon_registry

from .models import Error, Trace


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    provider_identity: str
    model_id: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class ProviderEmbedding:
    provider_identity: str
    model_id: str
    vector: tuple[float, ...]
    input_tokens: int | None = None
    cost_units: float | None = None


class EmbeddingProviderFailure(RuntimeError):
    def __init__(self, error: Error, trace: Trace) -> None:
        self.error = error
        self.trace = trace
        super().__init__(error.code)


class EmbeddingProviderAuthority(Protocol):
    async def active_binding(self, trace: Trace) -> ProviderBinding: ...

    async def embed(
        self, text: str, expected: ProviderBinding, trace: Trace
    ) -> ProviderEmbedding: ...


def _failure(code: str, trace: Trace, *, retryable: bool) -> EmbeddingProviderFailure:
    return EmbeddingProviderFailure(
        Error(code=code, message="Embedding provider request was not completed.", retryable=retryable),
        trace,
    )


class GideonEmbeddingProviderAuthority:
    """Resolve and call the one embedding binding selected by Gideon.

    The adapter never receives or returns credentials. Provider construction and
    secret resolution remain inside Gideon's provider registry.
    """

    async def active_binding(self, trace: Trace) -> ProviderBinding:
        fingerprint = gideon_registry.get_active_embedding_fingerprint()
        if fingerprint is None:
            raise _failure("EMBEDDING_PROVIDER_UNAVAILABLE", trace, retryable=False)
        provider_identity, model_id = fingerprint
        try:
            dimensions = await asyncio.to_thread(
                gideon_registry.get_active_embedding_dim
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._classified_failure(exc, trace) from None
        if not isinstance(dimensions, int) or dimensions <= 0:
            raise _failure("EMBEDDING_PROVIDER_UNAVAILABLE", trace, retryable=True)
        return ProviderBinding(provider_identity, model_id, dimensions)

    async def embed(
        self, text: str, expected: ProviderBinding, trace: Trace
    ) -> ProviderEmbedding:
        generation = gideon_registry.active_binding_key()
        before = gideon_registry.get_active_embedding_fingerprint()
        if before != (expected.provider_identity, expected.model_id):
            raise _failure("EMBEDDING_PROVIDER_CHANGED", trace, retryable=True)
        try:
            vector = await gideon_registry.embed_active(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._classified_failure(exc, trace) from None
        after = gideon_registry.get_active_embedding_fingerprint()
        if after != before or gideon_registry.active_binding_key() != generation:
            raise _failure("EMBEDDING_PROVIDER_CHANGED", trace, retryable=True)
        if vector is None:
            raise _failure("EMBEDDING_PROVIDER_REFUSED", trace, retryable=True)
        try:
            values = tuple(float(value) for value in vector)
        except (TypeError, ValueError, OverflowError):
            raise _failure("EMBEDDING_VECTOR_INVALID", trace, retryable=False) from None
        if len(values) != expected.dimensions:
            raise _failure("EMBEDDING_DIMENSION_MISMATCH", trace, retryable=False)
        if any(not math.isfinite(value) for value in values):
            raise _failure("EMBEDDING_VECTOR_INVALID", trace, retryable=False)
        return ProviderEmbedding(
            provider_identity=expected.provider_identity,
            model_id=expected.model_id,
            vector=values,
        )

    @staticmethod
    def _classified_failure(exc: BaseException, trace: Trace) -> EmbeddingProviderFailure:
        name = type(exc).__name__.lower()
        if "credential" in name or "authentication" in name:
            return _failure("EMBEDDING_CREDENTIAL_MISSING", trace, retryable=False)
        if "ratelimit" in name or "rate_limit" in name:
            return _failure("EMBEDDING_RATE_LIMITED", trace, retryable=True)
        if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
            return _failure("EMBEDDING_TRANSPORT_FAILED", trace, retryable=True)
        return _failure("EMBEDDING_PROVIDER_FAILED", trace, retryable=True)
