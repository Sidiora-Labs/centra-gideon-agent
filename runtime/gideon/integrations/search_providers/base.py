"""Shared search records and the provider extension contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

DEPTHS: tuple[str, ...] = ("quick", "balanced", "deep")
VALID_DEPTHS = frozenset(DEPTHS)
DEFAULT_DEPTH = "balanced"


def _record(
    source: Any,
    required: tuple[str, ...],
    *,
    nullable: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    values = {name: getattr(source, name) for name in required}
    for name in nullable + optional:
        value = getattr(source, name)
        include = value is not None if name in nullable else bool(value)
        if include:
            values[name] = value
    return values


def _select_depth(requested: str | None, supported: tuple[str, ...]) -> str:
    choices = ((requested or DEFAULT_DEPTH).strip().lower(), DEFAULT_DEPTH, *supported)
    return next((choice for choice in choices if choice in supported), DEFAULT_DEPTH)


@dataclass
class SearchCapabilities:
    returns_content: bool = False
    returns_answer: bool = False
    returns_highlights: bool = False
    supports_recency: bool = False
    supports_domains: bool = False
    supports_fetch: bool = False
    depths: tuple[str, ...] = DEPTHS
    keyless: bool = False

    def to_dict(self) -> dict[str, Any]:
        values = _record(
            self,
            (
                "returns_content",
                "returns_answer",
                "returns_highlights",
                "supports_recency",
                "supports_domains",
                "supports_fetch",
                "depths",
                "keyless",
            ),
        )
        values["depths"] = list(self.depths)
        return values


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    score: float | None = None
    published_date: str | None = None
    raw_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _record(
            self,
            ("url", "title", "snippet"),
            nullable=("score",),
            optional=("published_date", "raw_content"),
        )


@dataclass
class SearchResult:
    results: list[SearchHit] = field(default_factory=list)
    answer: str = ""
    provider: str = ""
    query: str = ""
    depth: str = DEFAULT_DEPTH

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(hit.url for hit in self.results if hit.url))

    def to_dict(self) -> dict[str, Any]:
        values = _record(
            self,
            ("results", "sources", "provider", "query", "depth"),
            optional=("answer",),
        )
        values["results"] = [hit.to_dict() for hit in self.results]
        return values


@dataclass
class FetchResult:
    url: str
    content: str = ""
    title: str = ""
    char_count: int = 0
    truncated: bool = False
    next_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _record(
            self,
            ("url", "content", "title", "char_count", "truncated"),
            nullable=("next_index",),
        )


class SearchProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    def capabilities(self) -> SearchCapabilities: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        depth: str = DEFAULT_DEPTH,
        recency: str | None = None,
        domains: list[str] | None = None,
        max_results: int = 10,
    ) -> SearchResult: ...

    async def fetch(
        self,
        url: str,
        *,
        max_tokens: int = 0,
        start_index: int = 0,
    ) -> FetchResult:
        raise NotImplementedError(f"{self.name} does not support fetch")

    def normalize_depth(self, depth: str | None) -> str:
        return _select_depth(depth, self.capabilities().depths)

    def info(self) -> dict[str, Any]:
        values = _record(self, ("name", "display_name"))
        values["capabilities"] = self.capabilities().to_dict()
        return values
