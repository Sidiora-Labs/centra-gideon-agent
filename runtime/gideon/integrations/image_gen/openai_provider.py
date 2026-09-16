"""OpenAI-compatible image requests with bounded execution and owned edit files."""

import asyncio
import base64
import importlib
import logging
import os
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from gideon.integrations.image_gen.provider import (
    ImageGenError,
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)

logger = logging.getLogger(__name__)
_GEN_TIMEOUT_S = 180


@dataclass(frozen=True)
class _ImageCall:
    operation: str
    parameters: dict[str, Any]
    source: str = ""
    mask: str = ""

    @contextmanager
    def arguments(self):
        with ExitStack() as handles:
            values = dict(self.parameters)
            if self.operation == "edit":
                paths = [("image", self.source)]
                if self.mask:
                    paths.append(("mask", self.mask))
                for field, path in paths:
                    values[field] = handles.enter_context(open(path, "rb"))
            yield values

    async def execute(self, client: Any, parse: Any, close: Any) -> list[ImageResult]:
        try:
            with self.arguments() as arguments:
                method = getattr(client.images, self.operation)
                response = await method(**arguments)
                return parse(response)
        finally:
            await close(client)


def _response_item(item: Any) -> ImageResult | None:
    sources = (
        ("b64", getattr(item, "b64_json", None)),
        ("url", getattr(item, "url", None)),
    )
    revised = getattr(item, "revised_prompt", None) or ""
    chosen = next(((field, value) for field, value in sources if value), None)
    if chosen is None:
        return None
    field, content = chosen
    return ImageResult(**{field: content}, mime="image/png", revised_prompt=revised)


class OpenAIImageProvider(ImageGenProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        provider_type: str = "",
        endpoint: str = "",
        api_key: str = "",
    ) -> None:
        self._provider_name = provider_name
        self._provider_type = provider_type
        self._endpoint = endpoint
        self._api_key = api_key

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return f"{self._provider_name} (remote image)"

    def _catalog(self):
        from gideon.integrations.media_catalogs import get_media_catalog

        return get_media_catalog("image_gen", self._provider_type)

    def _catalog_models(self) -> list[ImageGenModel]:
        catalog = self._catalog()
        models = []
        for entry in catalog.models if catalog else ():
            parameters = dict(name=entry.name, description=entry.description)
            parameters.update(
                sizes=list(entry.extra.get("sizes", [])),
                supports_edit=bool(entry.extra.get("supports_edit", False)),
            )
            models.append(ImageGenModel(**parameters))
        return models

    def _catalog_default(self) -> str:
        return getattr(self._catalog(), "default_model", "")

    async def is_available(self) -> bool:
        if self._resolve_api_key():
            try:
                importlib.import_module("openai")
            except ImportError:
                pass
            else:
                return True
        return False

    async def list_models(self) -> list[ImageGenModel]:
        models = self._catalog_models()
        if models:
            from gideon.integrations.image_gen.registry import active_image_gen

            selection = active_image_gen()
            active = ""
            if selection is not None and selection[0].name == self._provider_name:
                active = selection[1]
            models = [
                replace(model, downloaded=True, active=model.name == active)
                for model in models
            ]
        return models

    def _default_model(self, model: str) -> str:
        selected = model or self._catalog_default()
        if selected:
            return selected
        raise ImageGenError(
            f"No image model selected for {self._provider_name!r}, and this endpoint "
            f"has no contributed default — pin one in Settings → Models (Image · Generation)."
        )

    async def _dispatch(
        self,
        operation: str,
        prompt: str,
        model: str,
        size: str,
        count: int,
        source: str = "",
        mask: str = "",
    ) -> list[ImageResult]:
        selected = self._default_model(model)
        client = self._client()
        parameters = dict(model=selected, prompt=prompt, n=max(1, count))
        if size:
            parameters["size"] = size
        call = _ImageCall(operation, parameters, source, mask)
        execute = partial(call.execute, client, self._parse_response, self._close)
        return await self._await(execute, operation)

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        return await self._dispatch("generate", prompt, model, size, n)

    async def edit(
        self,
        prompt: str,
        *,
        source_image: str,
        mask: str = "",
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        return await self._dispatch("edit", prompt, model, size, n, source_image, mask)

    def _client(self) -> Any:
        try:
            sdk = importlib.import_module("openai")
        except ImportError as error:
            raise ImageGenError(
                "The openai SDK is not installed — cannot use remote image gen."
            ) from error
        key = self._resolve_api_key()
        if not key:
            raise ImageGenError(
                f"No API key configured for image provider {self._provider_name!r}."
            )
        return sdk.AsyncOpenAI(api_key=key, base_url=self._endpoint or None)

    async def _await(self, run: Any, op: str) -> list[ImageResult]:
        try:
            operation = run()
            return await asyncio.wait_for(operation, timeout=_GEN_TIMEOUT_S)
        except ImageGenError:
            raise
        except asyncio.TimeoutError as error:
            message = f"Image {op} timed out for provider {self._provider_name!r}."
            raise ImageGenError(message) from error
        except Exception as error:
            logger.exception(
                "Remote image %s failed for provider %r", op, self._provider_name
            )
            raise ImageGenError(f"Image {op} failed: {error}") from error

    @staticmethod
    def _parse_response(resp: Any) -> list[ImageResult]:
        candidates = map(_response_item, getattr(resp, "data", None) or ())
        images = [image for image in candidates if image is not None]
        if images:
            return images
        raise ImageGenError("Image provider returned no images.")

    @staticmethod
    async def _close(client: Any) -> None:
        with suppress(Exception):
            await client.close()

    def _resolve_api_key(self) -> str:
        return self._api_key or os.environ.get("OPENAI_API_KEY", "")


def decode_b64_image(b64: str) -> bytes:
    return base64.b64decode(b64)
