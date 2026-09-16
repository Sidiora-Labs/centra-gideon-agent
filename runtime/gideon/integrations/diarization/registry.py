"""Resolve registered speaker-separation engines without discovering remote adapters."""

from __future__ import annotations

from threading import RLock
from typing import Any

from gideon.integrations.diarization.provider import DiarizationProvider
from gideon.integrations.generation_catalog import resolve_selection

_providers: dict[str, DiarizationProvider] = {}
_catalog_lock = RLock()


def register_provider(provider: DiarizationProvider) -> None:
    global _providers
    with _catalog_lock:
        _providers = {**_providers, provider.name: provider}


def unregister_provider(name: str) -> None:
    global _providers
    with _catalog_lock:
        _providers = {key: value for key, value in _providers.items() if key != name}


def get_provider(name: str) -> DiarizationProvider | None:
    with _catalog_lock:
        return _providers.get(name)


def list_providers() -> list[DiarizationProvider]:
    with _catalog_lock:
        return [*_providers.values()]


def get_active_provider() -> DiarizationProvider | None:
    selected = active_diarization()
    if selected is not None:
        return selected[0]
    candidates = list_providers()
    return next(iter(candidates), None) if len(candidates) == 1 else None


def active_diarization() -> tuple[DiarizationProvider, str] | None:
    return resolve_selection("diarization", lambda: None, get_provider)


async def list_all_providers_info() -> list[dict[str, Any]]:
    selected = active_diarization()
    active_name = selected[0].name if selected is not None else ""
    rows = []
    for provider in list_providers():
        row = provider.info()
        row.update(
            available=await provider.is_available(), active=provider.name == active_name
        )
        rows.append(row)
    return rows
