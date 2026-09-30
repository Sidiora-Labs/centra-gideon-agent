"""Bundled model-price lookup and token charge arithmetic."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
_PRICING_FILE = Path(__file__).resolve().parent / "model_pricing.json"
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
    """Read-only compatibility view over the bundled model catalog."""

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


def builtin_rate(model: str) -> dict[str, float] | None:
    """Return the bundled row in the canonical routing-rate field names."""
    row = _rates(model)
    if row is None:
        return None
    return {
        "in_per_mtok": float(row.get("in", 0.0)),
        "out_per_mtok": float(row.get("out", 0.0)),
        "cache_read_per_mtok": float(row.get("cache_read", row.get("in", 0.0))),
        "cache_write_per_mtok": float(row.get("cache_write", 0.0)),
    }


@dataclass(frozen=True)
class TokenCharge:
    """Compatibility value object that delegates charge math to ``ModelRate``."""

    prompt: int = 0
    response: int = 0
    cached_read: int = 0
    cached_write: int = 0

    def at(self, row):
        from gideon.engine.routing.rates import ModelRate

        rate = ModelRate.from_obj(
            {
                "in_per_mtok": row.get("in", 0.0),
                "out_per_mtok": row.get("out", 0.0),
                "cache_read_per_mtok": row.get("cache_read", row.get("in", 0.0)),
                "cache_write_per_mtok": row.get("cache_write", 0.0),
            }
        )
        if rate is None:
            return 0.0
        return rate.cost(
            input_tokens=self.prompt,
            output_tokens=self.response,
            cache_read_tokens=self.cached_read,
            cache_creation_tokens=self.cached_write,
        )


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    provider: str = "",
    reported_cost_usd: float | None = None,
    provider_reported: bool | None = None,
) -> float:
    from gideon.engine.routing.rates import resolve_effective_price

    result = resolve_effective_price(
        provider,
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reported_cost_usd=reported_cost_usd,
        provider_reported=provider_reported,
    )
    return float(result.cost_usd or 0.0)


def has_pricing(model: str, *, provider: str = "") -> bool:
    from gideon.engine.routing.rates import rate_for

    return rate_for(provider, model) is not None


def cache_savings_usd(
    model: str,
    *,
    provider: str = "",
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    if not has_pricing(model, provider=provider):
        return None
    actual = estimate_cost(
        model,
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_creation_tokens,
        provider=provider,
    )
    uncached = (
        (input_tokens or 0) + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
    )
    hypothetical = estimate_cost(
        model, uncached, output_tokens, 0, 0, provider=provider
    )
    return round(hypothetical - actual, 6)
