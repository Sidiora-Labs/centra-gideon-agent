"""Resolve active embedding selections to native, contributed, or model adapters."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from gideon.integrations.embedding_providers.base import (
    EmbeddingModel,
    EmbeddingProvider,
    run_embed_sync,
)

logger = logging.getLogger(__name__)
_providers: dict[str, EmbeddingProvider] = {}
_catalog_guard = threading.RLock()
_scanned: dict[str, EmbeddingProvider] = {}
_scan_guard = threading.RLock()
_scanned_generation = -1
_revision = 0
_refresh_requested = False
_NATIVE_NAMES = ("sentence_transformers", "sentence-transformers", "native")
_NO_SELECTION = object()


def register_provider(provider: EmbeddingProvider) -> None:
    name = provider.name
    global _revision
    with _catalog_guard:
        _providers.update({name: provider})
        _scanned.pop(name, None)
        _revision += 1


def unregister_provider(name: str) -> None:
    global _revision
    with _catalog_guard:
        if name in _providers:
            del _providers[name]
            _scanned.pop(name, None)
            _revision += 1


def _registered(name: str) -> EmbeddingProvider | None:
    with _catalog_guard:
        return _providers.get(name)


def _ensure_scanned() -> None:
    global _scanned_generation, _revision, _refresh_requested
    from gideon.extensions.providers import media_scanners
    with _scan_guard:
        try:
            generation = media_scanners.generation()
            fresh = media_scanners.scan("embedding", strict=True)
            # A concurrent registration needs another scan; do not publish a mixed snapshot.
            if generation != media_scanners.generation():
                return
        except Exception:
            logger.debug("embedding scanner pass failed; keeping last good adapters", exc_info=True)
            return
        incoming = {provider.name: provider for provider in fresh if getattr(provider, "name", "")}
        with _catalog_guard:
            for name, provider in incoming.items():
                held = _providers.get(name)
                owned = held is not None and _scanned.get(name) is held
                if held is None or (owned and (_refresh_requested or type(held) is not type(provider))):
                    _providers[name] = _scanned[name] = provider
                    _revision += 1
            for name in set(_scanned).difference(incoming):
                if _providers.get(name) is _scanned[name]:
                    del _providers[name]
                    _revision += 1
                del _scanned[name]
            _scanned_generation = generation
            _refresh_requested = False


def refresh_providers() -> None:
    """Request an atomic rebuild of scanner-owned adapters from current settings."""
    global _refresh_requested
    with _scan_guard:
        _refresh_requested = True
    _ensure_scanned()


def active_binding_key() -> tuple:
    """Opaque process-local identity for consumer cache invalidation."""
    _ensure_scanned()
    specification = _active_embedding_spec()
    if specification is None:
        return (None,)
    name, model = specification
    with _catalog_guard:
        provider = _providers.get("native" if name in _NATIVE_NAMES else name)
    if provider is not None:
        return (specification, id(provider))
    try:
        from gideon.integrations.llm.registry import get_default_registry
        registry = get_default_registry()
        entry = registry.get_entry(name)
        return (specification, id(entry), id(registry.capability_of(entry.type)))
    except Exception:
        return (specification, None)


def get_provider(name: str) -> EmbeddingProvider | None:
    _ensure_scanned()
    return _registered(name)


def list_providers() -> list[EmbeddingProvider]:
    _ensure_scanned()
    with _catalog_guard:
        return [*_providers.values()]


def native_provider() -> EmbeddingProvider | None:
    return _registered("native")


async def _native_management(method: str, default: Any, *arguments) -> Any:
    provider = native_provider()
    if provider is None:
        return default
    try:
        return await getattr(provider, method)(*arguments)
    except Exception:
        logger.debug("Native embedding %s failed", method, exc_info=True)
        return default


async def list_native_models() -> list[EmbeddingModel]:
    return await _native_management("list_models", [])


async def delete_native_model(model_name: str) -> bool:
    return await _native_management("delete_model", False, model_name)


async def is_native_model_downloaded(model_name: str) -> bool:
    matches = (
        model for model in await list_native_models() if model.name == model_name
    )
    return any(model.downloaded for model in matches)


def ensure_registered() -> None:
    return None


def _active_embedding_spec() -> tuple[str, str] | None:
    from gideon.extensions.providers.use_cases import active_model_refs, split_ref

    first = next(iter(active_model_refs("embedding")), _NO_SELECTION)
    return split_ref(first) if isinstance(first, str) else None


def get_active_embedding_fingerprint() -> tuple[str, str] | None:
    """Return the safe provider/model identity for the current embedding binding.

    Resolve this on demand so a memory operation never treats a previous active
    selection as the current vector space. The tuple contains only the configured
    provider name and model reference; credentials and provider configuration are
    deliberately excluded.
    """
    specification = _active_embedding_spec()
    if not specification:
        return None
    provider, model = specification
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    return provider, model


def direct_provider(provider_name: str) -> EmbeddingProvider | None:
    """Look up a named typed adapter without consulting active model bindings."""
    if provider_name in _NATIVE_NAMES:
        ensure_registered()
        return native_provider()
    _ensure_scanned()
    return _registered(provider_name)


@dataclass(frozen=True)
class _Selection:
    provider_name: str
    model_id: str

    @property
    def native(self) -> bool:
        return self.provider_name in _NATIVE_NAMES

    def direct_provider(self) -> EmbeddingProvider | None:
        return direct_provider(self.provider_name)


@dataclass(frozen=True)
class _DirectBinding:
    provider: EmbeddingProvider
    model_id: str

    def one(self, text: str) -> list[float] | None:
        try:
            request = partial(self.provider.embed, text, model=self.model_id)
            return run_embed_sync(request, timeout=60)
        except Exception:
            logger.debug("Direct embedding failed", exc_info=True)
            return None

    def many(self, operation: Callable, texts: list[str]) -> list[list[float] | None]:
        request = partial(operation, texts, model=self.model_id)
        result = run_embed_sync(request, timeout=max(60.0, len(texts) * 5.0))
        vectors = list(result) if result is not None else []
        if len(vectors) < len(texts):
            vectors.extend([None] * (len(texts) - len(vectors)))
        return vectors[: len(texts)]


@dataclass(frozen=True)
class _ModelBinding:
    provider: Any
    embed: Callable
    model_id: str = ""

    async def request_many(self, texts: list[str]) -> list[list[float] | None]:
        try:
            await self.provider.start()
            if isinstance(self.provider, EmbeddingProvider):
                vectors = await self.provider.embed_batch(texts, model=self.model_id)
            else:
                vectors = await self.embed(texts)
            rows = list(vectors or [])
            return [list(row) if row is not None else None for row in rows]
        finally:
            await self.provider.shutdown()

    async def request(self, text: str) -> list[float] | None:
        rows = await self.request_many([text])
        return rows[0] if rows else None

    def many(self, texts: list[str]) -> list[list[float] | None]:
        rows = run_embed_sync(partial(self.request_many, texts), timeout=max(60.0, len(texts)*5.0))
        return (rows + [None]*len(texts))[:len(texts)]

    def one(self, text: str) -> list[float] | None:
        try:
            return run_embed_sync(partial(self.request, text), timeout=60)
        except Exception:
            logger.debug("Remote embed failed", exc_info=True)
            return None


def _build_model_provider(provider_name: str, model_id: str):
    from gideon.integrations.llm.registry import (
        ProviderResolutionError,
        get_default_registry,
        sync_entries_from_config,
    )

    registry = None
    for attempt in range(2):
        try:
            if registry is None:
                registry = get_default_registry()
            return registry.build(provider_name, embedding_model=model_id)
        except Exception as error:
            if attempt or not isinstance(error, ProviderResolutionError):
                phase = "after config sync" if attempt else "from LLM registry"
                logger.warning(
                    "Could not build embedding provider %r %s",
                    provider_name,
                    phase,
                    exc_info=True,
                )
                return None
            try:
                count = sync_entries_from_config()
            except Exception:
                logger.warning(
                    "Could not build embedding provider %r and config sync failed",
                    provider_name,
                    exc_info=True,
                )
                return None
            if not count:
                logger.warning(
                    "Embedding provider %r is not a configured provider entry (config.json providers[] has nothing to sync)",
                    provider_name,
                )
                return None
            logger.info(
                "Replayed %d config provider entr%s to resolve embedding provider %r",
                count,
                "y" if count == 1 else "ies",
                provider_name,
            )
    return None


def _llm_embed_fn(
    provider_name: str, model_id: str
) -> Callable[[str], list[float] | None] | None:
    provider = _build_model_provider(provider_name, model_id)
    if provider is None:
        return None
    operation = getattr(provider, "embed", None)
    if operation is None:
        logger.warning("Provider %r does not support embeddings", provider_name)
        return None
    return _ModelBinding(provider, operation, model_id).one


def _resolve_embed_fn() -> Callable[[str], list[float] | None] | None:
    specification = _active_embedding_spec()
    if specification is None:
        return None
    selection = _Selection(*specification)
    provider = selection.direct_provider()
    if selection.native:
        return provider.get_embed_fn(selection.model_id) if provider else None
    if provider is not None:
        return _DirectBinding(provider, selection.model_id).one
    return _llm_embed_fn(selection.provider_name, selection.model_id)


def _resolve_embed_many_fn() -> (
    Callable[[list[str]], list[list[float] | None]] | None
):
    specification = _active_embedding_spec()
    if specification is None:
        return None
    selection = _Selection(*specification)
    provider = selection.direct_provider()
    if provider is None:
        model_provider = _build_model_provider(*specification)
        if model_provider is None:
            return None
        operation = getattr(model_provider, "embed", None)
        return _ModelBinding(model_provider, operation, selection.model_id).many if callable(operation) else None
    operation = getattr(provider, "embed_batch", None)
    if not callable(operation):
        return None
    return partial(_DirectBinding(provider, selection.model_id).many, operation)


def get_active_embedding_dim() -> int | None:
    specification = _active_embedding_spec()
    if specification is None:
        return None
    selection = _Selection(*specification)
    provider = native_provider() if selection.native else None
    if provider is not None:
        try:
            list_models = getattr(provider, "list_models", None)
            if not callable(list_models):
                raise TypeError("native embedding provider cannot list models")
            models = run_embed_sync(list_models, timeout=30)
            for model in models:
                if model.name == selection.model_id:
                    return model.dimension
        except Exception:
            logger.debug(
                "native dim lookup via provider.list_models failed", exc_info=True
            )
    embed = get_active_embed_fn()
    vector = embed("dimension probe") if embed else None
    return len(vector) if vector else None


class _HotEmbedding:
    def __init__(self, *, many: bool = False):
        self._many = many

    def binding_key(self) -> tuple:
        return active_binding_key()

    def __call__(self, value):
        operation = _resolve_embed_many_fn() if self._many else _resolve_embed_fn()
        if operation is None:
            return [None] * len(value) if self._many else None
        return operation(value)


def get_active_embed_fn() -> Callable[[str], list[float] | None] | None:
    return _HotEmbedding() if _resolve_embed_fn() is not None else None


def get_active_embed_many_fn() -> Callable | None:
    return _HotEmbedding(many=True) if _resolve_embed_many_fn() is not None else None


async def embed_for(provider_name: str, model_id: str, text: str) -> list[float] | None:
    """Invoke exactly the named adapter/model with caller-owned cancellation."""
    selection = _Selection(provider_name, model_id)
    provider = selection.direct_provider()
    if provider is not None:
        return await provider.embed(text, model=selection.model_id)
    model_provider = _build_model_provider(provider_name, model_id)
    if model_provider is None:
        return None
    operation = getattr(model_provider, "embed", None)
    if not callable(operation):
        await model_provider.shutdown()
        return None
    return await _ModelBinding(model_provider, operation, selection.model_id).request(text)


async def embed_active(text: str) -> list[float] | None:
    """Invoke the current actual async adapter with cancellation owned by its caller."""
    specification = _active_embedding_spec()
    if specification is None:
        return None
    return await embed_for(*specification, text)
