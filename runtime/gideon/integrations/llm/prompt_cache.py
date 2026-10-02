"""Select a stable prompt boundary using a protocol-independent marker."""

import re
from dataclasses import dataclass
from enum import Enum


class PromptCache(str, Enum):
    NONE = "none"
    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


CACHE_HINT_KEY = "_cache_hint"
_VOLATILE_HINT_KEY = "_volatile"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class CacheBinding:
    generation: int
    policy_revision: int
    source_digest: str
    covered_digest: str

    def __post_init__(self) -> None:
        if self.generation < 1 or self.policy_revision < 1:
            raise ValueError("cache generation and policy revision must be positive")
        if _DIGEST.fullmatch(self.source_digest) is None:
            raise ValueError("cache source digest is invalid")
        if _DIGEST.fullmatch(self.covered_digest) is None:
            raise ValueError("cache covered digest is invalid")

    def validate(self, *, policy_revision: int, covered_digest: str) -> None:
        if self.policy_revision != policy_revision:
            raise ValueError("cached prompt policy revision does not match active policy")
        if self.covered_digest != covered_digest:
            raise ValueError("cached prompt covered digest does not match active context")

    def to_hint(self) -> dict[str, object]:
        return {
            "generation": self.generation,
            "policy_revision": self.policy_revision,
            "source_digest": self.source_digest,
            "covered_digest": self.covered_digest,
        }


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
    max_markers: int = 1, binding: CacheBinding | None = None,
    policy_revision: int | None = None, covered_digest: str | None = None,
) -> list[dict]:
    if not messages or mode is not PromptCache.EXPLICIT:
        return messages
    hint: dict[str, object] = {"generation": generation}
    if binding is not None:
        if policy_revision is None or covered_digest is None:
            raise ValueError(
                "cache binding requires active policy revision and covered digest"
            )
        binding.validate(
            policy_revision=policy_revision, covered_digest=covered_digest
        )
        if generation not in (0, binding.generation):
            raise ValueError("cache generation disagrees with cache binding")
        hint = binding.to_hint()
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
        dict(message, **{CACHE_HINT_KEY: hint})
        if index in boundaries else message
        for index, message in enumerate(messages)
    ]
