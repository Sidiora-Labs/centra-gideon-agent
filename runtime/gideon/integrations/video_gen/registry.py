"""Track contributed video engines and their active model selection."""

from threading import RLock
from typing import Any

from gideon.integrations.generation_catalog import (
    model_metadata,
    provider_metadata,
    reconcile_scanners,
    resolve_selection,
)
from gideon.integrations.video_gen.provider import VideoGenProvider

_providers: dict[str, VideoGenProvider] = {}
_scanner_names: set[str] = set()
_catalog_lock = RLock()
_MODEL_FIELDS = (
    "name",
    "description",
    "aspect_ratios",
    "max_duration_s",
    "downloaded",
    "active",
)


def register_provider(provider: VideoGenProvider) -> None:
    with _catalog_lock:
        _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    with _catalog_lock:
        _providers.pop(name, None)
        _scanner_names.discard(name)


def refresh_providers() -> None:
    with _catalog_lock:
        for name in _scanner_names.intersection(_providers):
            del _providers[name]
        _scanner_names.clear()


def _lookup(name: str) -> VideoGenProvider | None:
    with _catalog_lock:
        return _providers.get(name)


def _snapshot() -> list[VideoGenProvider]:
    with _catalog_lock:
        return [*_providers.values()]


def get_provider(name: str) -> VideoGenProvider | None:
    _ensure_scanned()
    return _lookup(name)


def _ensure_scanned() -> None:
    from gideon.extensions.providers.media_scanners import scan

    discovered = scan("video_gen")
    with _catalog_lock:
        reconcile_scanners(_providers, _scanner_names, discovered)


def list_providers() -> list[VideoGenProvider]:
    _ensure_scanned()
    return _snapshot()


def active_video_gen() -> tuple[VideoGenProvider, str] | None:
    return resolve_selection("video_gen", _ensure_scanned, _lookup)


def get_active_provider() -> VideoGenProvider | None:
    selected = active_video_gen()
    return selected[0] if selected is not None else None


async def list_all_providers_info() -> list[dict[str, Any]]:
    selected = active_video_gen()
    name = selected[0].name if selected is not None else ""
    return await provider_metadata(_snapshot(), name)


async def list_models_for_provider(provider_name: str) -> list[dict[str, Any]]:
    provider = _lookup(provider_name)
    if not provider:
        return []
    return model_metadata(await provider.list_models(), _MODEL_FIELDS)
