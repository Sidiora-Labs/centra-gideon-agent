"""Shared catalog operations for image and video generation adapters."""

from collections.abc import Callable, Iterable, MutableMapping
from typing import Any


def provider_identity(provider: Any) -> dict[str, Any]:
    return {name: getattr(provider, name) for name in ("name", "display_name")}


def reconcile_scanners(
    providers: MutableMapping[str, Any], tracked: set[str], candidates: Iterable[Any]
) -> None:
    incoming = {}
    for provider in candidates:
        name = getattr(provider, "name", "")
        if name:
            incoming[name] = provider
    for stale in tracked.difference(incoming):
        providers.pop(stale, None)
    providers.update(incoming)
    tracked.intersection_update(incoming)
    tracked.update(incoming)


def resolve_selection(
    capability: str, prepare: Callable[[], None], lookup: Callable[[str], Any]
) -> tuple[Any, str] | None:
    from gideon.extensions.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs(capability)
    selected = split_ref(refs[0]) if refs else None
    if selected is None:
        return None
    prepare()
    name, model = selected
    provider = lookup(name)
    return (provider, model) if provider is not None else None


async def provider_metadata(
    providers: Iterable[Any], active_name: str
) -> list[dict[str, Any]]:
    result = []
    for provider in providers:
        available = await provider.is_available()
        row = provider.info()
        row.update(available=available, active=provider.name == active_name)
        result.append(row)
    return result


def model_metadata(
    models: Iterable[Any], fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [{field: getattr(model, field) for field in fields} for model in models]
