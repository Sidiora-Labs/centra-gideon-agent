"""Model efficiency projections from persisted estimates and recent timings."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def _percentile(sorted_vals: list[float], pct: float) -> float:
    count = len(sorted_vals)
    if count == 0:
        return 0.0
    if count == 1:
        return round(sorted_vals[0], 1)
    position = min(count, max(1, math.ceil(pct / 100.0 * count))) - 1
    return round(sorted_vals[position], 1)


def _latencies_by_ref(
    audit_rows: list[dict], use_case: str, query_class: str
) -> dict[str, list[float]]:
    samples: dict = {}
    matching = (
        row
        for row in audit_rows
        if (row.get("use_case"), row.get("query_class")) == (use_case, query_class)
    )
    for row in matching:
        value = float(row.get("latency_ms", 0.0) or 0.0)
        if value > 0.0:
            ref = f"{row.get('provider', '')}:{row.get('model', '')}"
            samples.setdefault(ref, []).append(value)
    return samples


@dataclass(frozen=True)
class EfficiencyPoint:
    quality: float
    latency: float
    cost: float

    @classmethod
    def from_row(cls, row):
        latency = row["p50_ms"]
        return cls(
            row["success"],
            latency if latency > 0 else float("inf"),
            row["avg_cost_usd"],
        )

    def dominates(self, other):
        comparisons = (
            (self.quality, other.quality),
            (other.latency, self.latency),
            (other.cost, self.cost),
        )
        return all(a >= b for a, b in comparisons) and any(
            a > b for a, b in comparisons
        )


def _dominates(a: dict, b: dict) -> bool:
    return EfficiencyPoint.from_row(a).dominates(EfficiencyPoint.from_row(b))


def _mark_frontier(rows: list[dict]) -> None:
    points = [EfficiencyPoint.from_row(row) for row in rows]
    for index, (row, point) in enumerate(zip(rows, points)):
        row["on_frontier"] = not any(
            other.dominates(point) for slot, other in enumerate(points) if slot != index
        )


def _project(ref, estimate, samples):
    ordered = sorted(samples)
    row = {"ref": ref, "n": int(estimate.get("n", 0))}
    for target, source, precision in (
        ("success", "success_rate", 4),
        ("feedback", "feedback", 4),
        ("avg_cost_usd", "avg_cost_usd", 6),
    ):
        row[target] = round(float(estimate.get(source, 0.0)), precision)
    row.update(p50_ms=_percentile(ordered, 50), p95_ms=_percentile(ordered, 95))
    return row


def telemetry_rows(
    stats: dict[str, Any], audit_rows: list[dict], use_case: str, query_class: str
) -> list[dict]:
    estimates = stats.get("use_cases", {}).get(use_case, {}).get(query_class, {})
    samples = _latencies_by_ref(audit_rows, use_case, query_class)
    rows = [
        _project(ref, estimate, samples.get(ref, []))
        for ref, estimate in sorted(estimates.items())
    ]
    _mark_frontier(rows)
    return rows
