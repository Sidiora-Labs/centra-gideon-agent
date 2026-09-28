"""Resolve image delivery and image-reading against Gideon's bound models."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gideon.integrations.llm.base import ModelProvider

logger = logging.getLogger(__name__)

_TTL_SECS = 600.0
_LIST_TIMEOUT_SECS = 8.0
IMAGE_USE_CASE = "image_modality"
NO_IMAGE_MODEL = "No image model is set up."
BOUND_IMAGE_MODEL_UNAVAILABLE = "The image model chosen in Settings → Models can't run right now."

_tags_memo: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}


@dataclass(frozen=True)
class ImageInput:
    accepted: bool
    reason: str = ""
    model: str = ""


@dataclass(frozen=True)
class ImageReader:
    ref: str = ""
    bound: bool = False
    reason: str = ""


async def image_input(served_ref: str) -> ImageInput:
    """Whether a named, bound model and provider type can receive image pixels."""
    from gideon.extensions.providers.use_cases import split_ref

    parsed = split_ref(served_ref or "")
    if not parsed or not all(part.strip() for part in parsed):
        return ImageInput(False, "No chat model is chosen yet.")
    entry_name, model = parsed
    if model.lower() == "auto":
        return ImageInput(False, "No chat model is chosen yet.")

    try:
        from gideon.integrations.llm.registry import get_default_registry

        registry = get_default_registry()
        entry = registry.get_entry(entry_name)
        if not registry.capability_of(entry.type).supports_vision:
            return ImageInput(False, f"{model} can't take images.", model)
    except Exception:
        logger.debug("image input: no capability record for %r", entry_name, exc_info=True)
        return ImageInput(False, f"{model} can't take images.", model)

    tags = await _model_tags(registry, entry, model)
    if "image_modality" not in tags:
        return ImageInput(False, f"{model} can't take images.", model)
    return ImageInput(True, "", model)


async def image_reader() -> ImageReader:
    """Return the image-modality binding, or the chat model when it accepts pixels."""
    from gideon.extensions.providers.provider_bridge import can_resolve_use_case
    from gideon.extensions.providers.use_cases import active_model_refs

    bound = active_model_refs(IMAGE_USE_CASE)
    if bound:
        if can_resolve_use_case(IMAGE_USE_CASE):
            return ImageReader(ref=bound[0], bound=True)
        return ImageReader(reason=BOUND_IMAGE_MODEL_UNAVAILABLE)

    chat_refs = active_model_refs("chat")
    if chat_refs and (await image_input(chat_refs[0])).accepted:
        return ImageReader(ref=chat_refs[0])
    return ImageReader(reason=NO_IMAGE_MODEL)


async def resolve_image_reader(**kwargs: Any) -> ModelProvider:
    """Build only the named provider selected by :func:`image_reader`."""
    from gideon.extensions.providers.provider_bridge import resolve_provider_for_use_case
    from gideon.integrations.llm.registry import ProviderResolutionError

    reader = await image_reader()
    if not reader.ref:
        raise ProviderResolutionError(
            f"No image model can read this file. Choose one in Settings → Models."
        )
    return resolve_provider_for_use_case(
        IMAGE_USE_CASE,
        model_override=None if reader.bound else reader.ref,
        **kwargs,
    )


async def _model_tags(registry, entry, model: str) -> frozenset[str]:
    from gideon.integrations.llm.catalog import infer_capabilities

    key = (entry.name, model)
    now = time.monotonic()
    hit = _tags_memo.get(key)
    if hit is not None and now - hit[0] < _TTL_SECS:
        return hit[1]

    tags: frozenset[str] | None = None
    catalog = registry.build_catalog(entry)
    if catalog is not None:
        try:
            rows = await asyncio.wait_for(catalog.list_models(), timeout=_LIST_TIMEOUT_SECS)
            row = next((item for item in rows if model in (item.id, item.name)), None)
            if row is not None and row.capabilities:
                tags = frozenset(row.capabilities)
        except Exception:
            logger.debug("image input catalog listing failed for %r", entry.name, exc_info=True)
    if tags is None:
        tags = frozenset(infer_capabilities(model))
    _tags_memo[key] = (now, tags)
    return tags


def clear_cache() -> None:
    _tags_memo.clear()
