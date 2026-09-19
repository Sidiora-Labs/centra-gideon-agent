"""Configuration value conversion and explicit flag polarity."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


def _convert(value: object, fallback: T, converter: Callable[[Any], T]) -> T:
    try:
        return converter(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: object, default: int) -> int:
    return _convert(value, default, int)


def _safe_float(value: object, default: float) -> float:
    return _convert(value, default, float)


def _safe_choice(value: object, allowed: tuple[str, ...], default: str) -> str:
    try:
        normalized = str(value).strip().lower()
    except Exception:
        return default
    return next((option for option in allowed if option == normalized), default)


def _meta(label: str, help: str, **kwargs: object) -> dict:
    metadata: dict = {"label": label, "help": help}
    metadata.update(kwargs)
    return metadata


_GUARD_FALSE = frozenset(("0", "false", "no", "off", "disable", "disabled", "n", "f"))
_EXPOSE_TRUE = frozenset(("1", "true", "yes", "on", "enable", "enabled", "y", "t"))


@dataclass(frozen=True, slots=True)
class _FlagPolicy:
    fallback: bool
    recognized: frozenset[str]
    numeric: Callable[[int], bool]

    def read(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return self.numeric(value)
        if isinstance(value, str):
            recognized = value.strip().lower() in self.recognized
            return not self.fallback if recognized else self.fallback
        return self.fallback


_PROTECTION = _FlagPolicy(True, _GUARD_FALSE, lambda value: value != 0)
_EXPOSURE = _FlagPolicy(False, _EXPOSE_TRUE, lambda value: value == 1)


def _guard_flag(value: object) -> bool:
    return _PROTECTION.read(value)


def _expose_flag(value: object) -> bool:
    return _EXPOSURE.read(value)


def _num(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    prepared = value.strip() if isinstance(value, str) else value
    return _convert(prepared, default, float)


def _str_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    supported = (item for item in raw if isinstance(item, (str, int, float)))
    return [text for item in supported if (text := str(item)).strip()]
