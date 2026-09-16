"""Persist search bindings and apply the general-search default."""

import json
from pathlib import Path
from threading import RLock

from gideon.core.atomic_write import atomic_write

SEARCH_USE_CASES: tuple[str, ...] = (
    "search-general",
    "search-news",
    "search-financial",
    "fetch-article",
)
VALID_SEARCH_USE_CASES = frozenset(SEARCH_USE_CASES)
DEFAULT_SEARCH_USE_CASE = "search-general"

_binding_lock = RLock()


def _active_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / "active_search_providers.json"


def _provider_names(value: object) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return list(map(str, filter(None, value)))
    return None


class _BindingFile:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict[str, list[str]]:
        if not self.path.is_file():
            return {}
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(stored, dict):
            return {}
        decoded = ((key, _provider_names(value)) for key, value in stored.items())
        return {key: value for key, value in decoded if value is not None}

    def write(self, bindings: dict[str, list[str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, f"{json.dumps(bindings, indent=2)}\n")

    def bind(self, use_case: str, provider_name: str) -> None:
        bindings = self.read()
        if provider_name:
            bindings[use_case] = [provider_name]
        else:
            bindings.pop(use_case, None)
        self.write(bindings)


def load_active_search_providers() -> dict[str, list[str]]:
    with _binding_lock:
        return _BindingFile(_active_path()).read()


def save_active_search_providers(active: dict[str, list[str]]) -> None:
    with _binding_lock:
        _BindingFile(_active_path()).write(active)


def active_search_provider_names(use_case: str) -> list[str]:
    bindings = load_active_search_providers()
    choices = (use_case, DEFAULT_SEARCH_USE_CASE)
    for name in dict.fromkeys(choices):
        selected = bindings.get(name)
        if selected:
            return list(selected) if isinstance(selected, list) else []
    return []


def set_active_search_provider(use_case: str, provider_name: str) -> None:
    if use_case not in VALID_SEARCH_USE_CASES:
        raise ValueError(f"Invalid search use case: {use_case!r}")
    with _binding_lock:
        _BindingFile(_active_path()).bind(use_case, provider_name)
