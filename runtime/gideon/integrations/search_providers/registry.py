"""Resolve registered search adapters and execute their fallback policies."""

import logging
from dataclasses import dataclass
from threading import RLock
from typing import Any

from gideon.integrations.search_providers.base import (
    FetchResult,
    SearchProvider,
    SearchResult,
)

logger = logging.getLogger(__name__)
_providers: dict[str, SearchProvider] = {}
_catalog_lock = RLock()


def register_provider(provider: SearchProvider) -> None:
    with _catalog_lock:
        _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    with _catalog_lock:
        _providers.pop(name, None)


def get_provider(name: str) -> SearchProvider | None:
    with _catalog_lock:
        return _providers.get(name)


def list_providers() -> list[SearchProvider]:
    with _catalog_lock:
        return list(_providers.values())


def _is_keyless(provider: SearchProvider) -> bool:
    try:
        return provider.capabilities().keyless
    except Exception:
        return False


def _keyless_provider() -> SearchProvider | None:
    return next(
        (provider for provider in list_providers() if _is_keyless(provider)), None
    )


@dataclass(frozen=True)
class _CandidatePlan:
    providers: tuple[SearchProvider, ...]

    @classmethod
    def implicit(cls, use_case: str) -> "_CandidatePlan":
        keyed: list[SearchProvider] = []
        keyless: list[SearchProvider] = []
        for provider in list_providers():
            (keyless if _is_keyless(provider) else keyed).append(provider)
        ordered = (*keyed, *keyless)
        if use_case == "fetch-article":
            extractors = tuple(p for p in ordered if p.capabilities().supports_fetch)
            ordered = extractors or ordered
        return cls(ordered)

    async def resolve(self) -> SearchProvider | None:
        first = next(iter(self.providers), None)
        for candidate in self.providers:
            available = False
            try:
                available = bool(await candidate.is_available())
            except Exception:
                logger.debug(
                    "search provider %r availability probe failed",
                    candidate.name,
                    exc_info=True,
                )
            if available:
                return candidate
        return first


async def _first_available(candidates: list[SearchProvider]) -> SearchProvider | None:
    return await _CandidatePlan(tuple(candidates)).resolve()


async def resolve_search_provider_for_use_case(use_case: str) -> SearchProvider | None:
    from gideon.integrations.search_providers.use_cases import (
        VALID_SEARCH_USE_CASES,
        active_search_provider_names,
    )

    if use_case not in VALID_SEARCH_USE_CASES:
        raise ValueError(f"Unknown search use case: {use_case!r}")
    names = active_search_provider_names(use_case)
    if names:
        name = names[0]
        bound = get_provider(name)
        if bound is not None:
            return bound
        logger.info(
            "bound search provider %r for %r is not registered; falling back",
            name,
            use_case,
        )
    return await _CandidatePlan.implicit(use_case).resolve()


async def search_with_fallback(
    use_case: str, query: str, **kw: Any
) -> tuple[SearchResult | None, bool]:
    selected = await resolve_search_provider_for_use_case(use_case)
    retried = False
    while selected is not None:
        try:
            result = await selected.search(query, **kw)
        except Exception as error:
            if retried:
                raise
            alternate = _keyless_provider()
            if alternate is None or alternate.name == selected.name:
                raise
            logger.warning(
                "search via %r failed (%s); falling back to keyless %r",
                selected.name,
                error,
                alternate.name,
            )
            selected, retried = alternate, True
        else:
            return result, retried
    return None, False


async def _fetch_native(url: str, options: dict[str, Any]) -> FetchResult:
    from gideon.integrations.web.fetch import web_fetch

    outcome = await web_fetch(
        url,
        max_tokens=options.get("max_tokens", 0) or 5000,
        start_index=options.get("start_index", 0) or 0,
        require_provenance=False,
    )
    if not outcome.ok:
        raise RuntimeError(outcome.error or "fetch failed")
    fields = ("url", "content", "title", "char_count", "truncated", "next_index")
    return FetchResult(**{name: getattr(outcome, name) for name in fields})


async def fetch_with_fallback(url: str, **kw: Any) -> tuple[FetchResult, bool]:
    selected = await resolve_search_provider_for_use_case("fetch-article")
    supports_fetch = selected is not None and selected.capabilities().supports_fetch
    if supports_fetch:
        try:
            result = await selected.fetch(url, **kw)
        except Exception as error:
            logger.warning(
                "fetch via %r failed (%s); falling back to native pipeline",
                selected.name,
                error,
            )
        else:
            return result, False
    return await _fetch_native(url, kw), True


def can_resolve_search_use_case(use_case: str) -> bool:
    try:
        from gideon.integrations.search_providers.use_cases import (
            VALID_SEARCH_USE_CASES,
            active_search_provider_names,
        )
    except Exception:
        return False
    if use_case not in VALID_SEARCH_USE_CASES:
        return False
    names = active_search_provider_names(use_case)
    with _catalog_lock:
        registered = set(_providers)
    return bool(registered.intersection(names) or registered)
