"""Select a stable prompt boundary using a protocol-independent marker."""

from enum import Enum


class PromptCache(str, Enum):
    NONE = "none"
    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


CACHE_HINT_KEY = "_cache_hint"
_VOLATILE_HINT_KEY = "_volatile"


def effective_cache_mode(declared: PromptCache, *, enabled: bool) -> PromptCache:
    if enabled:
        return declared
    return PromptCache.NONE


def _cache_boundary(messages: list[dict]) -> int:
    boundary = 0
    for index, message in enumerate(messages):
        eligible = message.get("role") != "tool" and not message.get(_VOLATILE_HINT_KEY)
        if eligible:
            boundary = index
    return boundary


def mark_cacheable_prefix(
    messages: list[dict], mode: PromptCache, *, generation: int = 0
) -> list[dict]:
    if not messages or mode is not PromptCache.EXPLICIT:
        return messages
    boundary = _cache_boundary(messages)
    replacement = dict(
        messages[boundary], **{CACHE_HINT_KEY: {"generation": generation}}
    )
    return [
        replacement if index == boundary else message
        for index, message in enumerate(messages)
    ]
