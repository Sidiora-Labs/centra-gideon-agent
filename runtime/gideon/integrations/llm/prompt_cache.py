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
    messages: list[dict], mode: PromptCache, *, generation: int = 0,
    max_markers: int = 1,
) -> list[dict]:
    if not messages or mode is not PromptCache.EXPLICIT:
        return messages
    if max_markers <= 1:
        boundaries = {_cache_boundary(messages)}
    else:
        eligible = [
            index for index, message in enumerate(messages)
            if message.get("role") != "tool"
            and not message.get(_VOLATILE_HINT_KEY)
            and message.get("content")
        ]
        stable_system = next(
            (index for index in reversed(eligible) if messages[index].get("role") == "system"),
            None,
        )
        boundaries = set(eligible[-max_markers:])
        if stable_system is not None:
            boundaries.add(stable_system)
            if len(boundaries) > max_markers:
                boundaries.remove(min(index for index in boundaries if index != stable_system))
    return [
        dict(message, **{CACHE_HINT_KEY: {"generation": generation}})
        if index in boundaries else message
        for index, message in enumerate(messages)
    ]
