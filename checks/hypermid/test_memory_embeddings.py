from __future__ import annotations

import math
import os
from dataclasses import replace

import pytest

from gideon.hypermid.embeddings import (
    EmbeddingRegistration,
    EmbeddingRegistry,
    EmbeddingService,
    SemanticEmbeddingUnavailable,
    content_digest,
    validate_embedding,
)
from gideon.hypermid.models import Scope, Trace
from gideon.hypermid.providers import (
    GideonEmbeddingProviderAuthority,
    ProviderEmbedding,
)


def _scope() -> Scope:
    return Scope("owner", "project")


def _trace() -> Trace:
    return Trace("trace-embedding", "request-embedding")


def _registration() -> EmbeddingRegistration:
    return EmbeddingRegistration.create(
        registration_id="registration-1",
        scope=_scope(),
        mode="managed-service",
        provider_identity="centra",
        model_id="embedding-large-v1",
        dimensions=3,
    )


@pytest.mark.parametrize(
    "vector",
    [
        (0.0, 0.0, 0.0),
        (1.0, math.nan, 0.0),
        (1.0, math.inf, 0.0),
        (1.0, 2.0),
    ],
)
def test_invalid_vectors_are_rejected_without_publication(vector: tuple[float, ...]) -> None:
    registration = _registration()
    with pytest.raises(ValueError):
        validate_embedding(
            registration,
            content_digest("source"),
            ProviderEmbedding("centra", "embedding-large-v1", vector),
        )


def test_retired_registration_cannot_validate_an_inflight_result() -> None:
    registration = _registration()
    registry = EmbeddingRegistry()
    registry.activate(registration)
    assert registry.retire(registration.registration_id)
    assert registry.active(registration.scope) is None
    with pytest.raises(ValueError, match="not active"):
        validate_embedding(
            replace(registration, state="retired"),
            content_digest("source"),
            ProviderEmbedding(
                "centra", "embedding-large-v1", (0.25, 0.5, 0.75)
            ),
        )


@pytest.mark.asyncio
async def test_mode_off_degrades_hybrid_and_refuses_semantic_required() -> None:
    registry = EmbeddingRegistry()
    service = EmbeddingService(registry)
    await service.discover_and_activate(
        registration_id="off-1",
        scope=_scope(),
        mode="off",
        trace=_trace(),
    )
    hybrid = await service.embed_query(
        scope=_scope(),
        query="lexical remains available",
        mode="hybrid",
        semantic_required=False,
        trace=_trace(),
    )
    assert hybrid.embedding is None
    assert hybrid.semantic_unavailable
    assert hybrid.error is not None
    assert hybrid.error.code == "SEARCH_SEMANTIC_UNAVAILABLE"

    with pytest.raises(SemanticEmbeddingUnavailable) as raised:
        await service.embed_query(
            scope=_scope(),
            query="semantic required",
            mode="semantic",
            semantic_required=True,
            trace=_trace(),
        )
    assert raised.value.error.code == "SEARCH_SEMANTIC_UNAVAILABLE"
    assert raised.value.trace == _trace()


@pytest.mark.asyncio
async def test_real_gideon_provider_binding_and_vector() -> None:
    if os.environ.get("HYPERMID_REQUIRE_REAL_EMBEDDING") != "1":
        pytest.skip("real provider qualification is opt-in")
    authority = GideonEmbeddingProviderAuthority()
    binding = await authority.active_binding(_trace())
    response = await authority.embed(
        "Hypermid real embedding acceptance probe.", binding, _trace()
    )
    assert response.provider_identity == binding.provider_identity
    assert response.model_id == binding.model_id
    assert len(response.vector) == binding.dimensions
    assert all(math.isfinite(value) for value in response.vector)
    assert math.sqrt(sum(value * value for value in response.vector)) > 0.0
