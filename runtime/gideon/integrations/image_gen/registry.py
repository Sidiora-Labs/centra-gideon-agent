"""Discover image engines and resolve active generation models."""

from threading import RLock
from typing import Any

from gideon.integrations.generation_catalog import (
    model_metadata,
    provider_metadata,
    reconcile_scanners,
    resolve_selection,
)
from gideon.integrations.image_gen.provider import ImageGenProvider

_providers: dict[str, ImageGenProvider] = {}
_auto_registered = False
_scanner_names: set[str] = set()
_catalog_lock = RLock()
_MODEL_FIELDS = (
    "name",
    "description",
    "sizes",
    "supports_edit",
    "downloaded",
    "active",
)


def register_provider(provider: ImageGenProvider) -> None:
    with _catalog_lock:
        _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    with _catalog_lock:
        _providers.pop(name, None)


def get_provider(name: str) -> ImageGenProvider | None:
    with _catalog_lock:
        return _providers.get(name)


def list_providers() -> list[ImageGenProvider]:
    with _catalog_lock:
        return [*_providers.values()]


def _ensure_registered() -> None:
    global _auto_registered
    with _catalog_lock:
        initialize = not _auto_registered
        _auto_registered = True
        if initialize:
            _register_remote_providers()
            _register_stub_provider()
    _ensure_scanned()


def _ensure_scanned() -> None:
    from gideon.extensions.providers.media_scanners import scan

    discovered = scan("image_gen")
    with _catalog_lock:
        reconcile_scanners(_providers, _scanner_names, discovered)


def _register_remote_providers() -> None:
    from gideon.extensions.providers.use_cases import openai_family_providers
    from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider

    for entry in openai_family_providers():
        name = entry["name"]
        with _catalog_lock:
            if name in _providers:
                continue
            arguments = {key: entry[key] for key in ("endpoint", "api_key")}
            arguments.update(provider_name=name, provider_type=entry.get("type", ""))
            _providers[name] = OpenAIImageProvider(**arguments)


def _register_stub_provider() -> None:
    from os import environ

    from gideon.integrations.image_gen.stub_provider import StubImageProvider

    if environ.get("GIDEON_IMAGE_GEN_STUB") == "1":
        with _catalog_lock:
            if "stub" not in _providers:
                _providers["stub"] = StubImageProvider()


def refresh_providers() -> None:
    global _auto_registered
    from gideon.extensions.providers.use_cases import openai_family_providers

    transient = set(map(lambda entry: entry["name"], openai_family_providers()))
    transient.add("stub")
    with _catalog_lock:
        for name in transient.intersection(_providers):
            del _providers[name]
        _auto_registered = False


def active_image_gen() -> tuple[ImageGenProvider, str] | None:
    return resolve_selection("image_gen", _ensure_registered, get_provider)


def get_active_provider() -> ImageGenProvider | None:
    selected = active_image_gen()
    return selected[0] if selected is not None else None


async def list_all_providers_info() -> list[dict[str, Any]]:
    _ensure_registered()
    selected = active_image_gen()
    name = selected[0].name if selected is not None else ""
    return await provider_metadata(list_providers(), name)


async def list_models_for_provider(provider_name: str) -> list[dict[str, Any]]:
    _ensure_registered()
    provider = get_provider(provider_name)
    if not provider:
        return []
    return model_metadata(await provider.list_models(), _MODEL_FIELDS)
