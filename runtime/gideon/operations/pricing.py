"""Bundled model-price lookup and token charge arithmetic."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
_PRICING_FILE = Path(__file__).resolve().parent / "model_pricing.json"
_PER = 1000000.0
_PROVIDER_PREFIX = re.compile("^(?:[a-z]{2,6}\\.)?anthropic\\.")
_VERSION_TAIL = re.compile("-(\\d+)-(\\d+)(?=-\\d{8}|$)")


def _read_prices(path):
    if path.exists():
        try:
            with open(path, encoding="utf-8") as stream:
                document = json.load(stream)
            return {
                key: row
                for key, row in document.items()
                if not key.startswith("_") and isinstance(row, dict)
            }
        except (OSError, ValueError):
            logger.warning("Could not load model_pricing.json; cost estimates disabled")
    return {}


_PRICES: dict[str, dict[str, float]] = _read_prices(_PRICING_FILE)


def _canonical(model: str) -> str:
    family = _PROVIDER_PREFIX.sub("", model).removesuffix("-v1:0")
    return _VERSION_TAIL.sub(lambda match: f"-{match[1]}.{match[2]}", family, count=1)


@dataclass(frozen=True)
class PriceTable:
    rows: dict

    def lookup(self, model):
        if not model:
            return None
        candidates = dict.fromkeys((model, _canonical(model)))
        for candidate in candidates:
            exact = self.rows.get(candidate)
            if exact is not None:
                return exact
            key = max(
                (key for key in self.rows if candidate.startswith(key)),
                key=len,
                default=None,
            )
            if key is not None:
                return self.rows[key]
        return None


def _rates(model: str) -> dict[str, float] | None:
    return PriceTable(_PRICES).lookup(model)


@dataclass(frozen=True)
class TokenCharge:
    prompt: int = 0
    response: int = 0
    cached_read: int = 0
    cached_write: int = 0

    def at(self, row):
        input_rate = float(row.get("in", 0.0))
        prices = (
            input_rate,
            float(row.get("out", 0.0)),
            float(row.get("cache_read", input_rate)),
            float(row.get("cache_write", 0.0)),
        )
        tokens = (self.prompt, self.response, self.cached_read, self.cached_write)
        charges = [(count or 0) * price for count, price in zip(tokens, prices)]
        return round((charges[0] + charges[1] + charges[2] + charges[3]) / _PER, 6)


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    row = _rates(model)
    if row is None:
        return 0.0
    return TokenCharge(
        input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
    ).at(row)


def has_pricing(model: str) -> bool:
    return _rates(model) is not None


def cache_savings_usd(
    model: str,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    if _rates(model) is None:
        return None
    actual = estimate_cost(
        model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
    )
    uncached = (
        (input_tokens or 0) + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
    )
    hypothetical = estimate_cost(model, uncached, output_tokens, 0, 0)
    return round(hypothetical - actual, 6)
