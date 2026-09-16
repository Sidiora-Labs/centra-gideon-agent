"""Goal-kind vocabulary — the valid goal types + granularity dial settings. Pure data,
no deps; lives in the unified ``loop`` package so the goal kind + its classifier don't
reach back into the legacy ``loops`` package (cutover Slice 2e — self-containing
``loop/`` before the legacy engines are deleted)."""

from __future__ import annotations

GOAL_TYPES: frozenset[str] = frozenset({"verifiable", "open_ended", "monitor"})

GRANULARITIES: frozenset[str] = frozenset(
    {"quick", "balanced", "exhaustive", "forever"}
)
