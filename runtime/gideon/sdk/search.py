"""SDK: the search-provider ABC + data types + the use-case-bound resolution helpers.

Stable re-export of ``gideon.integrations.search_providers.*`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps. Covers
the ABC/types a search adapter implements PLUS the resolution surface a tool provider
uses to run a search over whatever the user bound in Settings → Search
(``search_with_fallback`` + the use-case vocabulary).
"""

from gideon.integrations.search_providers.base import (
    DEFAULT_DEPTH,
    VALID_DEPTHS,
    FetchResult,
    SearchCapabilities,
    SearchHit,
    SearchProvider,
    SearchResult,
)
from gideon.integrations.search_providers.registry import (  # noqa: F401
    search_with_fallback,
)
from gideon.integrations.search_providers.use_cases import (
    DEFAULT_SEARCH_USE_CASE,
    VALID_SEARCH_USE_CASES,
)

__all__ = [
    "SearchProvider",
    "SearchCapabilities",
    "SearchHit",
    "SearchResult",
    "FetchResult",
    "DEFAULT_DEPTH",
    "VALID_DEPTHS",
    "search_with_fallback",
    "DEFAULT_SEARCH_USE_CASE",
    "VALID_SEARCH_USE_CASES",
]
