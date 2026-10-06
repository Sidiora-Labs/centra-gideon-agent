"""Configured model prices, preserved independently of provider credentials."""


from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


def price_overrides(section: object) -> dict[str, dict[str, Any]]:
    if not isinstance(section, dict):
        return {}
    rows = section.get("overrides")
    if not isinstance(rows, dict):
        return {}
    return {key: deepcopy(row) for key, row in rows.items() if isinstance(key, str) and isinstance(row, dict)}


@dataclass
class ModelPricesConfig:
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
