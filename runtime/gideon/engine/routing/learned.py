"""Evidence-ranked model slots with confidence and price boundaries."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gideon.engine.routing import stats as _stats

logger = logging.getLogger(__name__)
_FAILSAFE_MSG = "learned_order failed — keeping the incoming order"


def _num(value: Any) -> float | None:
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if numeric:
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _rows_for(stats: Any, use_case: str, query_class: str) -> dict[str, Any]:
    current = stats
    for key in ("use_cases", use_case, query_class):
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _opinion(row: Any, *, min_samples: int) -> float | None:
    if isinstance(row, dict):
        count = _num(row.get("n"))
        if count is not None and count >= min_samples:
            values = tuple(
                _num(row.get(key, default))
                for key, default in (
                    ("success_rate", None),
                    ("feedback", 0.0),
                    ("feedback_n", 0),
                )
            )
            success, feedback, samples = values
            if success is not None and feedback is not None and samples is not None:
                return _stats._score(success, feedback, int(samples))
    return None


def _bands(
    by_score: list[int], scores: dict[int, float], width: float
) -> list[list[int]]:
    result = []
    start = 0
    while start < len(by_score):
        best = scores[by_score[start]]
        end = start + 1
        while end < len(by_score) and best - scores[by_score[end]] <= width:
            end += 1
        result.append(by_score[start:end])
        start = end
    return result


def _cost_order(
    band: list[int], refs: list[str], cost_of: Callable[[str], float] | None,
    latency: dict[int, float] | None = None,
) -> list[int]:
    slots = sorted(band)
    if cost_of is None or len(slots) < 2:
        return slots
    prices = {}
    for index in slots:
        try:
            value = _num(cost_of(refs[index]))
        except Exception:
            value = None
        if value is not None:
            prices[index] = value
    observed = latency or {}
    if len(prices) > 1 and all(index in observed for index in prices):
        price_low, price_high = min(prices.values()), max(prices.values())
        time_low = min(observed[index] for index in prices)
        time_high = max(observed[index] for index in prices)
        def rank(index: int) -> tuple[float, int]:
            price_range = price_high - price_low
            time_range = time_high - time_low
            price_score = (prices[index] - price_low) / price_range if price_range else 0.0
            time_score = (observed[index] - time_low) / time_range if time_range else 0.0
            return (0.7 * price_score + 0.3 * time_score, index)
        ranked = iter(sorted(prices, key=rank))
    else:
        ranked = iter(sorted(prices, key=lambda index: (prices[index], index)))
    return [next(ranked) if index in prices else index for index in slots]


@dataclass
class RankedSlots:
    refs: list[str]
    scores: dict[int, float]
    latency: dict[int, float]

    @classmethod
    def measure(cls, refs, rows, min_samples, local_keys, margin):
        from gideon.engine.routing.policy import is_local_ref

        scores = {}
        latency = {}
        for slot, ref in enumerate(refs):
            row = rows.get(ref)
            value = _opinion(row, min_samples=min_samples)
            if value is not None:
                scores[slot] = (
                    value
                    if is_local_ref(ref, local_keys=local_keys)
                    else value - margin
                )
                measured = _num(row.get("avg_ms")) if isinstance(row, dict) else None
                if measured is not None and measured > 0:
                    latency[slot] = measured
        return cls(refs, scores, latency)

    def arrange(self, width, cost_of):
        if len(self.scores) < 2:
            return list(self.refs)
        descending = sorted(self.scores, key=lambda slot: (-self.scores[slot], slot))
        chosen = iter(
            index
            for band in _bands(descending, self.scores, width)
            for index in _cost_order(band, self.refs, cost_of, self.latency)
        )
        result = [
            self.refs[next(chosen)] if slot in self.scores else ref
            for slot, ref in enumerate(self.refs)
        ]
        return result if sorted(result) == sorted(self.refs) else list(self.refs)


def learned_order(
    refs: list[str],
    *,
    use_case: str,
    query_class: str,
    stats: dict,
    hysteresis: float,
    cloud_quality_margin: float,
    local_keys: set[str],
    cost_of: Callable[[str], float] | None = None,
    min_samples: int = 5,
) -> list[str]:
    original = list(refs)
    if len(original) < 2:
        return original
    try:
        rows = _rows_for(stats, use_case, query_class)
        if not rows:
            return original
        margin = max(0.0, float(cloud_quality_margin))
        width = max(0.0, float(hysteresis))
        ranking = RankedSlots.measure(
            original, rows, min_samples, set(local_keys), margin
        )
        return ranking.arrange(width, cost_of)
    except Exception:
        logger.debug(_FAILSAFE_MSG, exc_info=True)
        return list(refs)
