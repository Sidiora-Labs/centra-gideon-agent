"""Resolve context capacity from the shared catalog and active chat binding."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CONTEXT_WINDOW = 200_000
# Conservative fallback when a local endpoint has not reported its served capacity.
# Actual server defaults vary with its configuration and available hardware.
LOCAL_SERVED_CONTEXT_WINDOW = 4096
_TOKENS_FILE = Path(__file__).resolve().parent / "model_tokens.json"
_WINDOWS: dict[str, int] | None = None
_SERVED_WINDOWS: dict[str, int] = {}


def register_served_context_window(model_id: str, capacity: object) -> bool:
    declared = declared_context_window(capacity)
    if not model_id or declared is None:
        return False
    _SERVED_WINDOWS[model_id.strip()] = declared
    return True


def served_context_window(model_id: str | None) -> int | None:
    if not model_id:
        return None
    return _resolve_window(model_id.strip(), _SERVED_WINDOWS, 0) or None


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


def declared_context_window(value: object) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("context_window")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
        parsed = int(numeric)
    except (ValueError, OverflowError):
        return None
    return parsed if parsed > 0 and parsed == numeric else None


def is_local_endpoint(endpoint: str | None) -> bool:
    hostname = urlparse(endpoint or "").hostname
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        return True
    try:
        address = ipaddress.ip_address(hostname)
        return address.is_loopback or address.is_private
    except ValueError:
        return False


def model_context_window(
    model_id: str | None,
    default: int = DEFAULT_CONTEXT_WINDOW,
    *,
    override: object = None,
    local: bool = False,
) -> int:
    declared = declared_context_window(override)
    if declared is not None:
        return declared
    served = served_context_window(model_id)
    if served is not None:
        return served
    if local:
        return LOCAL_SERVED_CONTEXT_WINDOW
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
