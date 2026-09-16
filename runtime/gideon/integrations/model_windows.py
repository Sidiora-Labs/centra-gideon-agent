"""Resolve context capacity from the shared catalog and active chat binding."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONTEXT_WINDOW = 200_000
_TOKENS_FILE = Path(__file__).resolve().parent / "model_tokens.json"
_WINDOWS: dict[str, int] | None = None


def _read_windows(path: Path) -> dict[str, int]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return {}
        accepted = {}
        for name, capacity in document.items():
            if name.startswith("_") or not isinstance(capacity, (int, float)):
                continue
            accepted[name] = int(capacity)
        return accepted
    except (OSError, ValueError, OverflowError):
        return {}


def _load() -> dict[str, int]:
    global _WINDOWS
    if _WINDOWS is None:
        _WINDOWS = _read_windows(_TOKENS_FILE)
    return _WINDOWS


def _resolve_window(identifier: str, windows: dict[str, int], default: int) -> int:
    if identifier in windows:
        return windows[identifier]
    remaining = identifier
    for separator in (":", "/"):
        head, divider, tail = remaining.partition(separator)
        if not divider:
            continue
        for candidate in (tail, head):
            if candidate in windows:
                return windows[candidate]
        remaining = tail
    normalized = remaining.lower().replace(".", "-")
    matching = (
        (key, value)
        for key, value in windows.items()
        if key
        and (
            key.lower().replace(".", "-") in normalized
            or normalized in key.lower().replace(".", "-")
        )
    )
    chosen = max(
        matching, key=lambda row: len(row[0].lower().replace(".", "-")), default=None
    )
    return default if chosen is None else chosen[1]


def model_context_window(
    model_id: str | None, default: int = DEFAULT_CONTEXT_WINDOW
) -> int:
    return _resolve_window(model_id.strip(), _load(), default) if model_id else default


def active_chat_model_window() -> int:
    try:
        from gideon.extensions.providers.use_cases import active_model_refs

        references = iter(active_model_refs("chat"))
        selected = next(references, None)
        if selected is not None:
            return model_context_window(selected)
    except Exception:
        pass
    return DEFAULT_CONTEXT_WINDOW
