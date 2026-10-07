from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_FIELDS = (
    "items",
    "input_tokens",
    "output_tokens",
    "requests",
    "cost_units",
    "retries",
    "wall_ms",
)


def _uint(value: object, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    minimum = 1 if positive else 0
    if value < minimum or value > 9_007_199_254_740_991:
        raise ValueError(f"{name} is outside the supported range")
    return value


@dataclass(frozen=True, slots=True)
class BudgetAmount:
    items: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    cost_units: int = 0
    retries: int = 0
    wall_ms: int = 0

    def __post_init__(self) -> None:
        for name in _FIELDS:
            _uint(getattr(self, name), name)

    def to_wire(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in _FIELDS}


@dataclass(frozen=True, slots=True)
class ModelBudget:
    max_items: int
    max_input_tokens: int
    max_output_tokens: int
    max_requests: int
    max_cost_units: int
    max_retries: int
    max_wall_ms: int

    def __post_init__(self) -> None:
        _uint(self.max_items, "max_items", positive=True)
        _uint(self.max_input_tokens, "max_input_tokens")
        _uint(self.max_output_tokens, "max_output_tokens")
        _uint(self.max_requests, "max_requests")
        _uint(self.max_cost_units, "max_cost_units")
        _uint(self.max_retries, "max_retries")
        _uint(self.max_wall_ms, "max_wall_ms", positive=True)

    def to_wire(self) -> dict[str, int]:
        return {
            "max_items": self.max_items,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_requests": self.max_requests,
            "max_cost_units": self.max_cost_units,
            "max_retries": self.max_retries,
            "max_wall_ms": self.max_wall_ms,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> ModelBudget:
        return cls(
            **{name: _uint(value.get(name), name) for name in cls.__dataclass_fields__}
        )


@dataclass(frozen=True, slots=True)
class ActualUsage:
    items: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    requests: int | None = None
    cost_units: int | None = None
    retries: int | None = None
    wall_ms: int | None = None

    def __post_init__(self) -> None:
        for name in _FIELDS:
            value = getattr(self, name)
            if value is not None:
                _uint(value, name)

    def to_wire(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in _FIELDS}

    @property
    def unknown_fields(self) -> frozenset[str]:
        return frozenset(name for name in _FIELDS if getattr(self, name) is None)
