"""Effective token rates from user overrides, provider declarations and bundled prices."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from gideon.core.config.pricing import price_overrides

logger = logging.getLogger(__name__)
_OVERLAY_FILE = "model_rates.json"
MAX_KEY_CHARS = 200
UNITS = {
    "token": "1M tokens",
    "image": "image",
    "second": "second of video",
    "minute": "minute of audio",
    "character": "1M characters",
}
UNIT_PRICE_FIELDS = {
    "image": "per_image",
    "second": "per_second",
    "minute": "per_minute",
    "character": "per_mchar",
}
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
    vendor: str = field(default="", compare=False)
    recorded: str = field(default="", compare=False)
    priced_as: str = field(default="", compare=False)

    @property
    def unit(self) -> str:
        return "token"

    def dearest_per_mtok(self) -> tuple[float, float]:
        return (
            max(
                self.in_per_mtok,
                self.cache_read_per_mtok or 0,
                self.cache_write_per_mtok or 0,
            ),
            self.out_per_mtok,
        )

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
            return replace(obj, source=source or obj.source)
        if not isinstance(obj, dict) or row_unit(obj) != "token":
            return None
        fields = ("in_per_mtok", "out_per_mtok")
        if isinstance(obj, dict) and any(name in obj for name in fields):
            try:
                if any(
                    isinstance(obj.get(name), bool)
                    for name in (
                        *fields,
                        "cache_read_per_mtok",
                        "cache_write_per_mtok",
                        "cache_read",
                        "cache_write",
                    )
                ):
                    return None
                values = [float(obj.get(name, 0.0) or 0.0) for name in fields]
                cache_read = obj.get("cache_read_per_mtok", obj.get("cache_read"))
                cache_write = obj.get("cache_write_per_mtok", obj.get("cache_write"))
                all_values = [
                    *values,
                    *(float(v) for v in (cache_read, cache_write) if v is not None),
                ]
                if any(not math.isfinite(v) or v < 0 for v in all_values):
                    return None
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


def row_unit(row: object) -> str:
    return (
        str(row.get("unit") or "token")
        if isinstance(row, dict)
        else str(getattr(row, "unit", "token"))
    )


def image_area(size: str) -> int | None:
    width, sep, height = str(size or "").strip().lower().partition("x")
    if not sep or not width.isdigit() or not height.isdigit():
        return None
    area = int(width) * int(height)
    return area if area > 0 else None


@dataclass(frozen=True)
class ImageTier:
    size: str = ""
    quality: str = ""
    per_image: float = 0.0

    def to_dict(self) -> dict:
        return {"size": self.size, "quality": self.quality, "per_image": self.per_image}


@dataclass(frozen=True)
class UnitRate:
    unit: str
    per_unit: float = 0.0
    tiers: tuple[ImageTier, ...] = ()
    default_size: str = ""
    default_quality: str = ""
    source: str = field(default="", compare=False)
    vendor: str = field(default="", compare=False)
    recorded: str = field(default="", compare=False)
    priced_as: str = field(default="", compare=False)

    def unit_price(self, *, size: str = "", quality: str = "") -> float | None:
        if not self.tiers:
            return self.per_unit
        quality = str(quality or self.default_quality).strip().lower()
        size = str(size or self.default_size).strip()
        area = image_area(size) if size else None
        if size and area is None:
            return None
        choices = []
        for tier in self.tiers:
            if tier.quality and tier.quality.lower() != quality:
                continue
            cover = image_area(tier.size) if tier.size else math.inf
            if cover is None or (tier.size and (area is None or cover < area)):
                continue
            choices.append(((cover, 0 if tier.quality else 1), tier.per_image))
        return min(choices)[1] if choices else None

    def cost(
        self, quantity: float | None, *, size: str = "", quality: str = ""
    ) -> float | None:
        price = self.unit_price(size=size, quality=quality)
        if price is None:
            return None
        if quantity is None:
            return 0.0 if price == 0 else None
        try:
            amount = float(quantity)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(amount) or amount < 0:
            return None
        return round(amount * price / (1_000_000 if self.unit == "character" else 1), 6)

    def to_dict(self) -> dict:
        row: dict[str, object] = {"unit": self.unit}
        if self.tiers:
            row.update(
                tiers=[tier.to_dict() for tier in self.tiers],
                default_size=self.default_size,
                default_quality=self.default_quality,
            )
        else:
            row[UNIT_PRICE_FIELDS[self.unit]] = self.per_unit
        return row

    @classmethod
    def from_obj(cls, obj: Any, *, source: str = "", unit: str) -> UnitRate | None:
        if isinstance(obj, cls):
            return (
                replace(obj, source=source or obj.source) if obj.unit == unit else None
            )
        if (
            unit not in UNIT_PRICE_FIELDS
            or not isinstance(obj, dict)
            or row_unit(obj) != unit
        ):
            return None
        try:
            if unit == "image" and obj.get("tiers"):
                values = obj["tiers"]
                if not isinstance(values, list) or len(values) > 24:
                    return None
                tiers = []
                for item in values:
                    if not isinstance(item, dict):
                        return None
                    if isinstance(item.get("per_image"), bool):
                        return None
                    amount = float(item["per_image"])
                    size, quality = str(item.get("size") or ""), str(
                        item.get("quality") or ""
                    )
                    if (
                        not math.isfinite(amount)
                        or amount < 0
                        or (size and image_area(size) is None)
                    ):
                        return None
                    tiers.append(ImageTier(size, quality, amount))
                default_size = str(obj.get("default_size") or "")
                if default_size and image_area(default_size) is None:
                    return None
                return cls(
                    unit,
                    tiers=tuple(tiers),
                    default_size=default_size,
                    default_quality=str(obj.get("default_quality") or ""),
                    source=source,
                )
            if isinstance(obj.get(UNIT_PRICE_FIELDS[unit]), bool):
                return None
            amount = float(obj[UNIT_PRICE_FIELDS[unit]])
            return (
                cls(unit, amount, source=source)
                if math.isfinite(amount) and amount >= 0
                else None
            )
        except (TypeError, ValueError, KeyError, OverflowError):
            return None


Rate = ModelRate | UnitRate


def _as_rate(row: Any, *, source: str = "", unit: str | None = None) -> Rate | None:
    actual = row_unit(row)
    if unit is not None and unit != actual:
        return None
    return (
        ModelRate.from_obj(row, source=source)
        if actual == "token"
        else UnitRate.from_obj(row, source=source, unit=actual)
    )


def ref_of(provider: str, model: str) -> str:
    from gideon.engine.routing.stats import ref_of as format_ref

    return format_ref(provider, model)


def is_local_provider_type(provider: str) -> bool:
    name = str(provider or "").strip().lower()
    return bool(name) and any(name.find(hint) >= 0 for hint in LOCAL_PROVIDER_HINTS)


def _resolve_home(home: Path | None) -> Path:
    if home is None:
        from gideon.core.config.loader import resolve_config_dir

        home = resolve_config_dir()
    return Path(home)


def _config_file(home: Path | None) -> Path:
    return _resolve_home(home) / "config.json"


def _empty_overlay():
    return {"version": RATES_VERSION, "rates": {}}


class RatesUnreadable(ValueError):
    pass


def _read_overrides(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError) as exc:
        raise RatesUnreadable(
            f"{path.name} could not be read; no model price was changed"
        ) from exc
    if not isinstance(document, dict):
        raise RatesUnreadable(
            f"{path.name} is not a settings object; no model price was changed"
        )
    section = document.get("model_prices")
    if section is not None and (
        not isinstance(section, dict)
        or ("overrides" in section and not isinstance(section["overrides"], dict))
    ):
        raise RatesUnreadable(
            "model_prices.overrides is not an object; no model price was changed"
        )
    return price_overrides(section)


def load_overlay(home: Path | None = None) -> dict[str, Any]:
    global _overlay_cache
    path = _config_file(home)
    try:
        stamp = path.stat()
        key = (str(path), stamp.st_ino, stamp.st_mtime_ns, stamp.st_size)
    except OSError:
        return _empty_overlay()
    if _overlay_cache is not None and _overlay_cache[0] == key:
        return _overlay_cache[1]
    try:
        result = {"version": RATES_VERSION, "rates": _read_overrides(path)}
    except RatesUnreadable:
        logger.warning(
            "config.json model prices unreadable; falling back to default prices",
            exc_info=True,
        )
        return _empty_overlay()
    _overlay_cache = key, result
    return result


def _mutate_overrides(change, *, home: Path | None = None):
    from gideon.core.config.transactions import mutate_config

    path = _config_file(home)

    def edit(document):
        section = document.get("model_prices")
        if section is not None and (
            not isinstance(section, dict)
            or ("overrides" in section and not isinstance(section["overrides"], dict))
        ):
            raise RatesUnreadable(
                "model_prices.overrides is not an object; no model price was changed"
            )
        current = price_overrides(section)
        result = change(current)
        document.setdefault("model_prices", {})["overrides"] = current
        return result

    return mutate_config(edit, path=path)


def save_overlay(rates: dict[str, Any], *, home: Path | None = None) -> Path:
    if not isinstance(rates, dict):
        raise ValueError("model prices must be an object")

    def replace_all(current):
        current.clear()
        current.update(rates)

    _mutate_overrides(replace_all, home=home)
    return _config_file(home)


def _recorded_day() -> str:
    from gideon.core.spend_day import today

    return today()


def rate_entry(body: object) -> tuple[str, dict]:
    if not isinstance(body, dict):
        raise ValueError("a model price must be an object")
    key = body.get("key")
    if (
        not isinstance(key, str)
        or not key.strip()
        or len(key) > MAX_KEY_CHARS
        or any(ord(c) < 32 for c in key)
    ):
        raise ValueError("give a model reference or pattern, at most 200 characters")
    rate = _as_rate(body)
    if rate is None:
        raise ValueError("give a valid non-negative price in the model's billed unit")
    return key.strip(), rate.to_dict()


def set_rate(key: str, row: dict, *, home: Path | None = None) -> Path:
    key, fields = rate_entry({**row, "key": key})
    fields["recorded"] = _recorded_day()
    _mutate_overrides(lambda current: current.update({key: fields}), home=home)
    return _config_file(home)


def clear_rate(key: str, *, home: Path | None = None) -> Path:
    _mutate_overrides(lambda current: current.pop(key, None), home=home)
    return _config_file(home)


def adopt_prices_set_before(*, home: Path | None = None) -> bool:
    path = _resolve_home(home) / _OVERLAY_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    try:
        document = json.loads(text)
        rows = document.get("rates") if isinstance(document, dict) else None
        if not isinstance(rows, dict):
            raise RatesUnreadable("saved model prices have no rates object")
        normalized = {}
        for key, row in rows.items():
            key, fields = (
                rate_entry({**row, "key": key})
                if isinstance(row, dict)
                else rate_entry(None)
            )
            normalized[key] = fields

        def adopt(current):
            for key, row in normalized.items():
                current.setdefault(key, row)

        _mutate_overrides(adopt, home=home)
    except (ValueError, OSError, RuntimeError):
        logger.warning(
            "previous model prices could not be adopted; the saved file was retained",
            exc_info=True,
        )
        return False
    try:
        if path.read_text(encoding="utf-8") == text:
            path.unlink()
    except (OSError, UnicodeError):
        return False
    return True


def _match_key(
    table: dict[str, Any], candidates: list[str], unit: str | None = None
) -> Any:
    for candidate in candidates:
        exact = table.get(candidate)
        if exact is not None and _as_rate(exact, unit=unit) is not None:
            return exact
        patterns = (
            key
            for key in table
            if isinstance(key, str)
            and any(marker in key for marker in "*?[")
            and fnmatchcase(candidate, key)
            and _as_rate(table[key], unit=unit) is not None
        )
        best = max(patterns, key=len, default=None)
        if best is not None:
            return table[best]
    return None


def _overlay_rate(
    provider: str, model: str, home: Path | None, unit: str | None = "token"
) -> Rate | None:
    table = load_overlay(home).get("rates", {})
    row = _match_key(table, [ref_of(provider, model), model], unit)
    rate = _as_rate(row, source="overlay", unit=unit)
    return (
        replace(rate, recorded=str(row.get("recorded") or ""))
        if rate is not None
        else None
    )


def _app_default_rate(
    provider: str, model: str, unit: str | None = "token"
) -> Rate | None:
    try:
        from gideon.integrations.llm.branded_specs import spec_pricing

        table = spec_pricing(provider)
        row = _match_key(dict(table), [model], unit) if table else None
        rate = _as_rate(row, source="app_default", unit=unit)
        return replace(rate, vendor=provider) if rate is not None else None
    except Exception:
        logger.warning(
            "app pricing lookup failed for provider %r", provider, exc_info=True
        )
        return None


def _builtin_rate(model: str, unit: str | None = "token") -> Rate | None:
    from gideon.operations.pricing import price_row

    found = price_row(model)
    if found is None or (unit is not None and found.unit != unit):
        return None
    row = dict(found.fields)
    if found.unit == "token":
        row = {
            "in_per_mtok": row.get("in"),
            "out_per_mtok": row.get("out"),
            "cache_read_per_mtok": row.get("cache_read"),
            "cache_write_per_mtok": row.get("cache_write"),
        }
    rate = _as_rate(row, source="builtin", unit=unit)
    return (
        replace(rate, vendor=found.vendor, recorded=found.recorded, priced_as=found.key)
        if rate is not None
        else None
    )


@dataclass(frozen=True)
class RateLookup:
    provider: str
    model: str
    home: Path | None
    unit: str | None = "token"
    overrides: bool = True

    def candidates(self):
        from gideon.integrations.llm.registry import serving_is_local, serving_type

        provider_type = serving_type(self.provider)
        if self.overrides:
            yield _overlay_rate(self.provider, self.model, self.home, self.unit)
            if provider_type != self.provider:
                yield _overlay_rate(provider_type, self.model, self.home, self.unit)
        if serving_is_local(self.provider, model=self.model):
            yield (
                ModelRate(0, 0, source="local")
                if self.unit in (None, "token")
                else UnitRate(self.unit, 0, source="local")
            )
        yield _app_default_rate(provider_type, self.model, self.unit)
        yield _builtin_rate(self.model, self.unit)

    def resolve(self) -> Rate | None:
        if not str(self.model or "").strip():
            return None
        return next((value for value in self.candidates() if value is not None), None)


def unit_rate_for(
    provider: str, model: str, unit: str, *, home: Path | None = None
) -> UnitRate | None:
    if unit not in UNIT_PRICE_FIELDS:
        return None
    rate = RateLookup(provider, model, home, unit).resolve()
    return rate if isinstance(rate, UnitRate) else None


def _view_fields(rate: Rate | None) -> dict:
    empty: dict[str, object] = {
        "source": "",
        "vendor": "",
        "recorded": "",
        "priced_as": "",
        "unit": "",
        "in_per_mtok": None,
        "out_per_mtok": None,
        "cache_read_per_mtok": None,
        "cache_write_per_mtok": None,
        "per_unit": None,
        "tiers": [],
        "default_size": "",
        "default_quality": "",
    }
    if rate is None:
        return empty
    empty.update(
        source=rate.source,
        vendor=rate.vendor,
        recorded=rate.recorded,
        priced_as=rate.priced_as,
        unit=rate.unit,
    )
    if isinstance(rate, ModelRate):
        empty.update(rate.to_dict())
    else:
        empty.update(
            per_unit=rate.per_unit if not rate.tiers else None,
            tiers=[tier.to_dict() for tier in rate.tiers],
            default_size=rate.default_size,
            default_quality=rate.default_quality,
        )
    return empty


def rates_view(models, *, home: Path | None = None) -> dict:
    try:
        stored = _read_overrides(_config_file(home))
        unreadable = ""
    except RatesUnreadable as error:
        stored, unreadable = {}, str(error)
    rows = []
    for key, value in sorted(stored.items()):
        rate = _as_rate(value, source="overlay")
        if rate is not None:
            rows.append(
                {
                    "key": key,
                    **_view_fields(rate),
                    **rate.to_dict(),
                    "recorded": str(value.get("recorded") or ""),
                }
            )
    listed = []
    for provider, model in dict.fromkeys(models):
        if not provider or not model:
            continue
        rate = RateLookup(provider, model, home, None).resolve()
        default = (
            RateLookup(provider, model, home, rate.unit, False).resolve()
            if rate is not None and rate.source == "overlay"
            else None
        )
        listed.append(
            {
                "ref": ref_of(provider, model),
                "priced": rate is not None,
                **_view_fields(rate),
                "default": _view_fields(default) if default is not None else None,
            }
        )
    return {"rates": rows, "models": listed, "unreadable": unreadable}


def rate_for(
    provider: str, model: str, *, home: Path | None = None
) -> ModelRate | None:
    rate = RateLookup(provider, model, home).resolve()
    return rate if isinstance(rate, ModelRate) else None


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

    @property
    def dollars(self) -> float:
        return float(self.cost_usd or 0)


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
    reported_ok = provider_reported is True or (
        provider_reported is None and reported is not None and reported > 0.0
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


def price_units(
    provider: str,
    model: str,
    unit: str,
    quantity: float | None,
    *,
    size: str = "",
    quality: str = "",
    home: Path | None = None,
) -> EffectiveModelPrice:
    rate = unit_rate_for(provider, model, unit, home=home)
    amount = (
        rate.cost(quantity, size=size, quality=quality) if rate is not None else None
    )
    return EffectiveModelPrice(
        provider,
        model,
        amount,
        amount is not None,
        rate.source if rate is not None and amount is not None else "unknown",
        rate is not None and rate.source != "local",
    )


__all__ += [
    "UnitRate",
    "ImageTier",
    "UNITS",
    "UNIT_PRICE_FIELDS",
    "RatesUnreadable",
    "rate_entry",
    "set_rate",
    "clear_rate",
    "rates_view",
    "unit_rate_for",
    "price_units",
    "adopt_prices_set_before",
]
