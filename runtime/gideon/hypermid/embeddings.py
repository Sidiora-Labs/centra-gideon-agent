"""Guarded Hypermid embedding registrations, validation, and jobs."""

from __future__ import annotations

import asyncio
import hashlib
import math
import threading
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from .models import Error, Scope, Trace
from .providers import (
    EmbeddingProviderAuthority,
    EmbeddingProviderFailure,
    GideonEmbeddingProviderAuthority,
    ProviderBinding,
    ProviderEmbedding,
)

EmbeddingMode = Literal["off", "local", "remote-compatible", "managed-service"]


def _fingerprint(
    mode: EmbeddingMode,
    provider_identity: str,
    model_id: str,
    dimensions: int,
    normalized: bool,
) -> str:
    material = (
        "hypermid.embedding.v1\0"
        f"{mode}\0{provider_identity}\0{model_id}\0{dimensions}\0cosine\0"
        f"{'true' if normalized else 'false'}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scope_key(scope: Scope) -> tuple[str, str, str | None]:
    return scope.owner_id, scope.project_id, scope.workspace_id


@dataclass(frozen=True, slots=True)
class EmbeddingRegistration:
    registration_id: str
    scope: Scope
    mode: EmbeddingMode
    provider_identity: str
    model_id: str
    dimensions: int
    normalized: bool
    fingerprint: str
    state: Literal["active", "retired", "failed"] = "active"

    @classmethod
    def create(
        cls,
        *,
        registration_id: str,
        scope: Scope,
        mode: EmbeddingMode,
        provider_identity: str,
        model_id: str,
        dimensions: int,
        normalized: bool = False,
    ) -> EmbeddingRegistration:
        if mode not in ("off", "local", "remote-compatible", "managed-service"):
            raise ValueError("unsupported embedding mode")
        if not registration_id or not provider_identity or not model_id:
            raise ValueError("embedding registration identifiers must be non-empty")
        if dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        return cls(
            registration_id=registration_id,
            scope=scope,
            mode=mode,
            provider_identity=provider_identity,
            model_id=model_id,
            dimensions=dimensions,
            normalized=normalized,
            fingerprint=_fingerprint(
                mode, provider_identity, model_id, dimensions, normalized
            ),
        )


@dataclass(frozen=True, slots=True)
class ValidatedEmbedding:
    registration_id: str
    fingerprint: str
    input_digest: str
    vector: tuple[float, ...]
    dimensions: int
    norm: float
    input_tokens: int | None = None
    cost_units: float | None = None


def validate_embedding(
    registration: EmbeddingRegistration,
    input_digest: str,
    response: ProviderEmbedding,
) -> ValidatedEmbedding:
    expected_fingerprint = _fingerprint(
        registration.mode,
        registration.provider_identity,
        registration.model_id,
        registration.dimensions,
        registration.normalized,
    )
    if registration.fingerprint != expected_fingerprint:
        raise ValueError("embedding registration fingerprint mismatch")
    if registration.state != "active":
        raise ValueError("embedding registration is not active")
    if registration.mode == "off":
        raise ValueError("embedding mode is off")
    if (
        response.provider_identity != registration.provider_identity
        or response.model_id != registration.model_id
    ):
        raise ValueError("embedding provider or model substitution")
    values = tuple(float(value) for value in response.vector)
    if len(values) != registration.dimensions:
        raise ValueError("embedding dimension mismatch")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("embedding contains a non-finite component")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= math.ulp(1.0):
        raise ValueError("embedding has zero norm")
    if registration.normalized and not math.isclose(norm, 1.0, abs_tol=1.0e-3):
        raise ValueError("embedding normalization mismatch")
    return ValidatedEmbedding(
        registration_id=registration.registration_id,
        fingerprint=registration.fingerprint,
        input_digest=input_digest,
        vector=values,
        dimensions=len(values),
        norm=norm,
        input_tokens=response.input_tokens,
        cost_units=response.cost_units,
    )


class EmbeddingRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[tuple[str, str, str | None], EmbeddingRegistration] = {}
        self._all: dict[str, EmbeddingRegistration] = {}

    def activate(self, registration: EmbeddingRegistration) -> None:
        if registration.state != "active":
            raise ValueError("only active registrations can be activated")
        with self._lock:
            key = _scope_key(registration.scope)
            incumbent = self._active.get(key)
            if (
                incumbent is not None
                and incumbent.registration_id != registration.registration_id
            ):
                self._all[incumbent.registration_id] = replace(
                    incumbent, state="retired"
                )
            self._active[key] = registration
            self._all[registration.registration_id] = registration

    def active(self, scope: Scope) -> EmbeddingRegistration | None:
        with self._lock:
            return self._active.get(_scope_key(scope))

    def retire(self, registration_id: str) -> bool:
        with self._lock:
            registration = self._all.get(registration_id)
            if registration is None or registration.state != "active":
                return False
            retired = replace(registration, state="retired")
            self._all[registration_id] = retired
            key = _scope_key(registration.scope)
            if self._active.get(key) == registration:
                del self._active[key]
            return True


@dataclass(frozen=True, slots=True)
class EmbeddingTarget:
    record_id: str
    revision_digest: str
    content_digest: str
    content: str


@dataclass(frozen=True, slots=True)
class PublicationGuard:
    target: EmbeddingTarget
    registration_id: str
    registration_fingerprint: str


class GuardedEmbeddingPublisher(Protocol):
    async def guarded_publish(
        self,
        *,
        expected: PublicationGuard,
        embedding: ValidatedEmbedding,
        actor_scope: Scope,
        trace: Trace,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class QueryEmbeddingResult:
    embedding: ValidatedEmbedding | None
    semantic_unavailable: bool
    error: Error | None
    trace: Trace


class SemanticEmbeddingUnavailable(RuntimeError):
    def __init__(self, error: Error, trace: Trace) -> None:
        self.error = error
        self.trace = trace
        super().__init__(error.code)


class EmbeddingService:
    def __init__(
        self,
        registry: EmbeddingRegistry,
        provider: EmbeddingProviderAuthority | None = None,
    ) -> None:
        self._registry = registry
        self._provider = provider or GideonEmbeddingProviderAuthority()

    async def discover_and_activate(
        self,
        *,
        registration_id: str,
        scope: Scope,
        mode: EmbeddingMode,
        trace: Trace,
        normalized: bool = False,
    ) -> EmbeddingRegistration:
        if mode == "off":
            registration = EmbeddingRegistration.create(
                registration_id=registration_id,
                scope=scope,
                mode=mode,
                provider_identity="off",
                model_id="off",
                dimensions=1,
                normalized=False,
            )
        else:
            binding = await self._provider.active_binding(trace)
            registration = EmbeddingRegistration.create(
                registration_id=registration_id,
                scope=scope,
                mode=mode,
                provider_identity=binding.provider_identity,
                model_id=binding.model_id,
                dimensions=binding.dimensions,
                normalized=normalized,
            )
        self._registry.activate(registration)
        return registration

    async def embed_text(
        self,
        *,
        scope: Scope,
        text: str,
        trace: Trace,
    ) -> ValidatedEmbedding:
        registration = self._registry.active(scope)
        if registration is None or registration.mode == "off":
            raise EmbeddingProviderFailure(
                Error(
                    code="EMBEDDING_PROVIDER_UNAVAILABLE",
                    message="Embedding is disabled for this scope.",
                    retryable=False,
                ),
                trace,
            )
        expected = ProviderBinding(
            registration.provider_identity,
            registration.model_id,
            registration.dimensions,
        )
        response = await self._provider.embed(text, expected, trace)
        current = self._registry.active(scope)
        if current != registration:
            raise EmbeddingProviderFailure(
                Error(
                    code="EMBEDDING_REGISTRATION_RETIRED",
                    message="Embedding registration changed while inference was running.",
                    retryable=True,
                ),
                trace,
            )
        try:
            return validate_embedding(registration, content_digest(text), response)
        except (TypeError, ValueError, OverflowError):
            raise EmbeddingProviderFailure(
                Error(
                    code="EMBEDDING_VECTOR_INVALID",
                    message="Embedding provider returned an invalid vector.",
                    retryable=False,
                ),
                trace,
            ) from None

    async def embed_and_publish(
        self,
        *,
        scope: Scope,
        actor_scope: Scope,
        target: EmbeddingTarget,
        publisher: GuardedEmbeddingPublisher,
        trace: Trace,
    ) -> bool:
        registration = self._registry.active(scope)
        if registration is None:
            raise EmbeddingProviderFailure(
                Error(
                    code="EMBEDDING_PROVIDER_UNAVAILABLE",
                    message="No active embedding registration exists.",
                    retryable=False,
                ),
                trace,
            )
        if content_digest(target.content) != target.content_digest:
            return False
        guard = PublicationGuard(
            target=target,
            registration_id=registration.registration_id,
            registration_fingerprint=registration.fingerprint,
        )
        embedding = await self.embed_text(scope=scope, text=target.content, trace=trace)
        current = self._registry.active(scope)
        if current != registration:
            return False
        return await publisher.guarded_publish(
            expected=guard,
            embedding=embedding,
            actor_scope=actor_scope,
            trace=trace,
        )

    async def embed_query(
        self,
        *,
        scope: Scope,
        query: str,
        mode: Literal["lexical", "semantic", "hybrid"],
        semantic_required: bool,
        trace: Trace,
    ) -> QueryEmbeddingResult:
        if mode == "lexical":
            return QueryEmbeddingResult(None, False, None, trace)
        try:
            embedding = await self.embed_text(scope=scope, text=query, trace=trace)
        except EmbeddingProviderFailure as failure:
            unavailable = Error(
                code="SEARCH_SEMANTIC_UNAVAILABLE",
                message="Semantic retrieval is unavailable for this request.",
                retryable=failure.error.retryable,
                retry_after_ms=failure.error.retry_after_ms,
            )
            if mode == "semantic" or semantic_required:
                raise SemanticEmbeddingUnavailable(unavailable, trace) from None
            return QueryEmbeddingResult(None, True, unavailable, trace)
        return QueryEmbeddingResult(embedding, False, None, trace)


async def wait_for_registration_change(
    registry: EmbeddingRegistry,
    scope: Scope,
    original: EmbeddingRegistration,
    *,
    timeout_seconds: float,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if registry.active(scope) != original:
            return True
        await asyncio.sleep(0)
    return False
