"""Effective token rates from user overrides, provider declarations and bundled prices."""

from __future__ import annotations

import json
import logging
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
    "LOCAL_PROVIDER_HINTS",
    "RATES_VERSION",
    "ModelRate",
    "cost_for",
    "is_local_provider_type",
    "load_overlay",
    "rate_for",
    "ref_of",
    "save_overlay",
]


@dataclass(frozen=True)
class ModelRate:
    in_per_mtok: float
    out_per_mtok: float
    source: str = field(default="", compare=False)

    def cost(self, *, input_tokens: int = 0, output_tokens: int = 0) -> float:
        charges = (
            (input_tokens or 0) * self.in_per_mtok,
            (output_tokens or 0) * self.out_per_mtok,
        )
        return round((charges[0] + charges[1]) / 1_000_000.0, 6)

    def to_dict(self) -> dict[str, float]:
        return dict(
            zip(("in_per_mtok", "out_per_mtok"), (self.in_per_mtok, self.out_per_mtok))
        )

    @classmethod
    def from_obj(cls, obj: Any, *, source: str = "") -> ModelRate | None:
        if isinstance(obj, ModelRate):
            return ModelRate(obj.in_per_mtok, obj.out_per_mtok, source or obj.source)
        fields = ("in_per_mtok", "out_per_mtok")
        if isinstance(obj, dict) and any(name in obj for name in fields):
            try:
                values = [float(obj.get(name, 0.0) or 0.0) for name in fields]
                return cls(*values, source=source)
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
        from gideon.operations.pricing import estimate_cost, has_pricing

        if has_pricing(model):
            amounts = [
                float(estimate_cost(model, **{key: 1_000_000}))
                for key in ("input_tokens", "output_tokens")
            ]
            return ModelRate(*amounts, source="builtin")
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
        if is_local_provider_type(self.provider):
            yield ModelRate(0.0, 0.0, source="local")
        yield _app_default_rate(self.provider, self.model)
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
    home: Path | None = None,
) -> float | None:
    rate = rate_for(provider, model, home=home)
    return (
        None
        if rate is None
        else rate.cost(input_tokens=input_tokens, output_tokens=output_tokens)
    )
