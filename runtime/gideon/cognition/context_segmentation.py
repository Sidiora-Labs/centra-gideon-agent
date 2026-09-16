"""Partition transcripts at semantic drift, with bounded turn-count fallback."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

DEFAULT_DRIFT_THRESHOLD = 0.6
DEFAULT_TURNS_PER_SEGMENT = 8
EmbedFn = Callable[[str], "list[float] | None"]


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    messages: list[dict]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    magnitudes = [
        math.sqrt(sum(value * value for value in vector)) for vector in (a, b)
    ]
    if not all(magnitudes):
        return 0.0
    return sum(left * right for left, right in zip(a, b)) / (
        magnitudes[0] * magnitudes[1]
    )


def _user_text(m: dict) -> str:
    if m.get("role") != "user":
        return ""
    return (m.get("content") or "").strip()


class _TopicBoundaries:
    def __init__(self, messages):
        self.messages = messages
        self.user_positions = [
            index for index, row in enumerate(messages) if row.get("role") == "user"
        ]

    def semantic(self, embed, threshold):
        previous = None
        for position in self.user_positions:
            text = _user_text(self.messages[position])
            vector = embed(text) if text else None
            if vector is None:
                continue
            if (
                previous is not None
                and _cosine(previous, vector) < threshold
                and position != 0
            ):
                yield position
            previous = vector

    def materialize(self, boundaries):
        starts = sorted(
            {start for start in boundaries if 0 <= start < len(self.messages)}
        )
        if starts[0] != 0:
            starts.insert(0, 0)
        return [
            Segment(start, end, self.messages[start:end])
            for start, end in zip(starts, starts[1:] + [len(self.messages)])
            if end > start
        ]


def _turn_count_boundaries(
    user_positions: list[int], turns_per_segment: int
) -> list[int]:
    stride = max(1, turns_per_segment)
    return [0, *user_positions[stride::stride]]


def segment_messages(
    messages: list[dict],
    *,
    embed_fn: EmbedFn | None = None,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
    turns_per_segment: int = DEFAULT_TURNS_PER_SEGMENT,
) -> list[Segment]:
    if not messages:
        return []
    topics = _TopicBoundaries(messages)
    if len(topics.user_positions) < 2:
        return [Segment(0, len(messages), list(messages))]
    boundaries = [0]
    if embed_fn is not None:
        boundaries.extend(topics.semantic(embed_fn, drift_threshold))
    if len(boundaries) == 1:
        boundaries = _turn_count_boundaries(topics.user_positions, turns_per_segment)
    return topics.materialize(boundaries)
