"Execution invariants parsed from workflow runtime hints."

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionHints:
    """The parsed `execution` group. One field, because one field has a reader."""

    single_active_feature: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> ExecutionHints:
        value = raw.get("single_active_feature") if isinstance(raw, dict) else None
        return cls(single_active_feature=_flag(value))


def from_runtime_hints(runtime_hints: Any) -> ExecutionHints:
    group = runtime_hints.get("execution") if isinstance(runtime_hints, dict) else None
    return ExecutionHints.from_dict(group)


def _flag(raw: Any) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "yes", "1", "on")
    return isinstance(raw, (int, float)) and bool(raw)
