"""Effective token rates from user overrides, provider declarations and bundled prices."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_OVERLAY_FILE = "model_rates.json"
RATES_VERSION = 1
_overlay_cache: tuple[tuple[str, int, int, int], dict[str, Any]] | None = None

LOCAL_PROVIDER_HINTS: frozenset[str] = frozenset(
    {
        "ollama",
        "lmstudio",
        "lm-studio",
        "llamacpp",
        "llama-cpp",
        "llama.cpp",
        "vllm",
        "localai",
    }
)
__all__ = [
    "EffectiveModelPrice",
    "LOCAL_PROVIDER_HINTS",
    "RATES_VERSION",
    "ModelRate",
    "cost_for",
    "is_local_provider_type",
    "load_overlay",
    "rate_for",
    "resolve_effective_price",
    "ref_of",
    "save_overlay",
]


@dataclass(frozen=True)
class ModelRate:
    in_per_mtok: float
    out_per_mtok: float
    source: str = field(default="", compare=False)
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None

    def cost(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        charges = (
            (input_tokens or 0) * self.in_per_mtok,
            (output_tokens or 0) * self.out_per_mtok,
            (cache_read_tokens or 0)
            * (
                self.in_per_mtok
                if self.cache_read_per_mtok is None
                else self.cache_read_per_mtok
            ),
            (cache_creation_tokens or 0) * (self.cache_write_per_mtok or 0.0),
        )
        return round(sum(charges) / 1_000_000.0, 6)

    def to_dict(self) -> dict[str, float]:
        values = dict(
            zip(("in_per_mtok", "out_per_mtok"), (self.in_per_mtok, self.out_per_mtok))
        )
        if self.cache_read_per_mtok is not None:
            values["cache_read_per_mtok"] = self.cache_read_per_mtok
        if self.cache_write_per_mtok is not None:
            values["cache_write_per_mtok"] = self.cache_write_per_mtok
        return values

    @classmethod
    def from_obj(cls, obj: Any, *, source: str = "") -> ModelRate | None:
        if isinstance(obj, ModelRate):
            return ModelRate(
                obj.in_per_mtok,
                obj.out_per_mtok,
                source or obj.source,
                obj.cache_read_per_mtok,
                obj.cache_write_per_mtok,
            )
        fields = ("in_per_mtok", "out_per_mtok")
        if isinstance(obj, dict) and any(name in obj for name in fields):
            try:
                values = [float(obj.get(name, 0.0) or 0.0) for name in fields]
                cache_read = obj.get("cache_read_per_mtok", obj.get("cache_read"))
                cache_write = obj.get("cache_write_per_mtok", obj.get("cache_write"))
                return cls(
                    values[0],
                    values[1],
                    source=source,
                    cache_read_per_mtok=(
                        None if cache_read is None else float(cache_read)
                    ),
                    cache_write_per_mtok=(
                        None if cache_write is None else float(cache_write)
                    ),
                )
            except (ValueError, TypeError):
                pass
        return None


def ref_of(provider: str, model: str) -> str:
    from gideon.engine.routing.stats import ref_of as format_ref

    return format_ref(provider, model)


def is_local_provider_type(provider: str) -> bool:
    name = str(provider or "").strip().lower()
    return bool(name) and any(name.find(hint) >= 0 for hint in LOCAL_PROVIDER_HINTS)


def _overlay_path(home: Path) -> Path:
    return Path(home).joinpath(_OVERLAY_FILE)


def _resolve_home(home: Path | None) -> Path:
    if home is None:
        from gideon.core.config.loader import config_dir

        home = config_dir()
    return Path(home)


def _empty_overlay():
    return {"version": RATES_VERSION, "rates": {}}


@dataclass(frozen=True)
class RateOverlay:
    path: Path

    def identity(self):
        stamp = os.stat(self.path)
        return (
            str(self.path),
            int(stamp.st_ino),
            int(stamp.st_mtime_ns),
            int(stamp.st_size),
        )

    def read(self):
        global _overlay_cache
        try:
            key = self.identity()
        except OSError:
            return _empty_overlay()
        if _overlay_cache is not None and _overlay_cache[0] == key:
            return _overlay_cache[1]
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            logger.warning(
                "model_rates.json unreadable — falling back to app defaults",
                exc_info=True,
            )
            return _empty_overlay()
        if not isinstance(document, dict) or not isinstance(
            document.get("rates"), dict
        ):
            logger.warning(
                "model_rates.json has no 'rates' object — falling back to app defaults"
            )
            return _empty_overlay()
        overlay = {
            "version": int(document.get("version", RATES_VERSION) or RATES_VERSION),
            "rates": document["rates"],
        }
        _overlay_cache = key, overlay
        return overlay

    def write(self, rates):
        document = {"version": RATES_VERSION, "rates": rates}
        atomic_write(self.path, json.dumps(document, indent=2, sort_keys=True) + "\n")
        return self.path


def load_overlay(home: Path | None = None) -> dict[str, Any]:
    return RateOverlay(_overlay_path(_resolve_home(home))).read()


def save_overlay(rates: dict[str, Any], *, home: Path | None = None) -> Path:
    return RateOverlay(_overlay_path(_resolve_home(home))).write(rates)


def _match_key(table: dict[str, Any], candidates: list[str]) -> Any:
    for candidate in candidates:
        exact = table.get(candidate)
        if exact is not None:
            return exact
        patterns = (
            key
            for key in table
            if isinstance(key, str)
            and any(marker in key for marker in "*?[")
            and fnmatchcase(candidate, key)
        )
        best = max(patterns, key=len, default=None)
        if best is not None:
            return table[best]
    return None


def _overlay_rate(provider: str, model: str, home: Path | None) -> ModelRate | None:
    table = load_overlay(home).get("rates", {})
    if isinstance(table, dict) and table:
        return ModelRate.from_obj(
            _match_key(table, [ref_of(provider, model), model]), source="overlay"
        )
    return None


def _app_default_rate(provider: str, model: str) -> ModelRate | None:
    try:
        import gideon.sdk.model
        from gideon.integrations.llm.branded_specs import spec_pricing

        table = spec_pricing(provider)
    except Exception:
        logger.warning(
            "app pricing lookup failed for provider %r", provider, exc_info=True
        )
        return None
    return (
        ModelRate.from_obj(_match_key(dict(table), [model]), source="app_default")
        if table
        else None
    )


def _builtin_rate(model: str) -> ModelRate | None:
    try:
        from gideon.operations.pricing import builtin_rate

        return ModelRate.from_obj(builtin_rate(model), source="builtin")
    except Exception:
        logger.warning(
            "builtin pricing lookup failed for model %r", model, exc_info=True
        )
    return None


@dataclass(frozen=True)
class RateLookup:
    provider: str
    model: str
    home: Path | None

    def candidates(self):
        yield _overlay_rate(self.provider, self.model, self.home)
        from gideon.integrations.llm.registry import serving_is_local, serving_type

        provider_type = serving_type(self.provider)
        if provider_type != self.provider:
            yield _overlay_rate(provider_type, self.model, self.home)
        if serving_is_local(self.provider):
            yield ModelRate(0.0, 0.0, source="local")
        yield _app_default_rate(provider_type, self.model)
        yield _builtin_rate(self.model)

    def resolve(self):
        if not str(self.model or "").strip():
            return None
        return next((value for value in self.candidates() if value is not None), None)


def rate_for(
    provider: str, model: str, *, home: Path | None = None
) -> ModelRate | None:
    return RateLookup(provider, model, home).resolve()


def cost_for(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    home: Path | None = None,
) -> float | None:
    result = resolve_effective_price(
        provider,
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        home=home,
    )
    return result.cost_usd


@dataclass(frozen=True)
class EffectiveModelPrice:
    provider: str
    model: str
    cost_usd: float | None
    priced: bool
    source: str
    estimated: bool


def resolve_effective_price(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    reported_cost_usd: float | None = None,
    provider_reported: bool | None = None,
    home: Path | None = None,
) -> EffectiveModelPrice:
    """Resolve cost, priced state and provenance at the single spend seam.

    ``provider_reported`` can explicitly validate a reported zero. Without that
    signal, only a positive cost is inferred as provider-reported so an absent
    default zero never masquerades as a billable observation.
    """
    try:
        reported = float(reported_cost_usd) if reported_cost_usd is not None else None
    except (TypeError, ValueError):
        reported = None
    reported_ok = (
        provider_reported is True
        or (provider_reported is None and reported is not None and reported > 0.0)
    )
    if (
        reported_ok
        and reported is not None
        and math.isfinite(reported)
        and reported >= 0
    ):
        return EffectiveModelPrice(
            provider, model, reported, True, "provider_reported", False
        )
    rate = rate_for(provider, model, home=home)
    if rate is None:
        return EffectiveModelPrice(provider, model, None, False, "unknown", False)
    return EffectiveModelPrice(
        provider,
        model,
        rate.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ),
        True,
        rate.source,
        rate.source != "local",
    )
