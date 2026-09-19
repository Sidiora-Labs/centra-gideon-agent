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
_NATIVE_NAMES = ("sentence_transformers", "sentence-transformers", "native")
_NO_SELECTION = object()


def register_provider(provider: EmbeddingProvider) -> None:
    name = provider.name
    with _catalog_guard:
        _providers.update({name: provider})


def unregister_provider(name: str) -> None:
    with _catalog_guard:
        if name in _providers:
            del _providers[name]


def _registered(name: str) -> EmbeddingProvider | None:
    with _catalog_guard:
        return _providers.get(name)


def _ensure_scanned() -> None:
    try:
        from gideon.extensions.providers.media_scanners import scan

        for provider in scan("embedding"):
            name = getattr(provider, "name", "")
            if name:
                with _catalog_guard:
                    _providers.setdefault(name, provider)
    except Exception:
        logger.debug("embedding scanner pass failed", exc_info=True)


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
    return None if first is _NO_SELECTION else split_ref(first)


@dataclass(frozen=True)
class _Selection:
    provider_name: str
    model_id: str

    @property
    def native(self) -> bool:
        return self.provider_name in _NATIVE_NAMES

    def direct_provider(self) -> EmbeddingProvider | None:
        if self.native:
            ensure_registered()
            return native_provider()
        _ensure_scanned()
        return _registered(self.provider_name)


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
        return list(result or [])


@dataclass(frozen=True)
class _ModelBinding:
    provider: Any
    embed: Callable

    async def request(self, text: str) -> list[float] | None:
        await self.provider.start()
        vectors = await self.embed([text])
        return list(vectors[0]) if vectors else None

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
    return _ModelBinding(provider, operation).one


def get_active_embed_fn() -> Callable[[str], list[float] | None] | None:
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


def get_active_embed_many_fn() -> (
    Callable[[list[str]], list[list[float] | None]] | None
):
    specification = _active_embedding_spec()
    if specification is None:
        return None
    selection = _Selection(*specification)
    provider = selection.direct_provider()
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
