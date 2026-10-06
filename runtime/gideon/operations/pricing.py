"""Bundled model-price lookup and token charge arithmetic."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any
from pathlib import Path

logger = logging.getLogger(__name__)
_PRICING_FILE = Path(__file__).resolve().parent / "model_pricing.json"
_PROFILE = re.compile(r"^[a-z]{2,8}(?:-[a-z]+)?\.(?=[a-z0-9-]+\.[^.])")
_API_REVISION = re.compile(r"-v\d+:\d+$")
_VERSION_TAIL = re.compile(r"-(\d{1,2})-(\d{1,2})(?=-\d{8}|@|\[|$)")
_SNAPSHOT = re.compile(r"(?:-\d{4,}|@|\[)")

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
    family = _PROFILE.sub("", model, count=1)
    family = _API_REVISION.sub("", family.removeprefix("anthropic."))
    return _VERSION_TAIL.sub(lambda match: f"-{match[1]}.{match[2]}", family, count=1)


@dataclass(frozen=True)
class PriceRow:
    key: str
    fields: Mapping[str, Any]
    vendor: str = ""
    recorded: str = ""

    @property
    def unit(self) -> str:
        return str(self.fields.get("unit") or "token")


@dataclass(frozen=True)
class PriceTable:
    rows: dict

    def lookup_row(self, model: str) -> PriceRow | None:
        if not model:
            return None
        base = _PROFILE.sub("", model, count=1)
        candidates = tuple(dict.fromkeys((model, base, _canonical(model))))
        for candidate in candidates:
            if candidate in self.rows:
                row = self.rows[candidate]
                return PriceRow(candidate, row, str(row.get("vendor") or ""), str(row.get("recorded") or ""))
        for candidate in candidates:
            key = max((key for key in self.rows if candidate.startswith(key) and _SNAPSHOT.match(candidate, len(key))), key=len, default=None)
            if key is not None:
                row = self.rows[key]
                return PriceRow(key, row, str(row.get("vendor") or ""), str(row.get("recorded") or ""))
        return None

    def lookup(self, model):
        found = self.lookup_row(model)
        return found.fields if found is not None else None


def price_row(model: str) -> PriceRow | None:
    return PriceTable(_PRICES).lookup_row(model)


def _rates(model: str) -> dict[str, float] | None:
    return PriceTable(_PRICES).lookup(model)


def builtin_rate(model: str) -> dict[str, float] | None:
    """Return the bundled row in the canonical routing-rate field names."""
    row = _rates(model)
    if row is None or row.get("unit", "token") != "token":
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
