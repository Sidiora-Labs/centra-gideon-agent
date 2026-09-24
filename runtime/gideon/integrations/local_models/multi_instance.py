"""One local-model management entry backed by an app's enabled instances."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from gideon.integrations.local_models.provider import (
    CapabilitySelfTestResult,
    LocalModel,
    LocalModelFailureCode,
    LocalModelProvider,
)

logger = logging.getLogger(__name__)


def instance_identity(provider: Any) -> dict[str, str]:
    return {
        "instance_id": str(getattr(provider, "instance_id", "") or provider.name),
        "instance_label": str(
            getattr(provider, "instance_label", "")
            or getattr(provider, "display_name", "")
            or getattr(provider, "instance_id", "")
            or provider.name
        ),
    }


class MultiInstanceLocalProvider(LocalModelProvider):
    def __init__(self, name: str, providers: list[Any]) -> None:
        self._name = name
        self._searchable_override: bool | None = None
        self.providers: list[Any] = []
        self.add(providers)

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return (
            str(getattr(self.providers[0], "display_name", self.name))
            if self.providers
            else self.name
        )

    @property
    def searchable(self) -> bool:
        if self._searchable_override is not None:
            return self._searchable_override
        return any(
            getattr(provider, "searchable", False) for provider in self.providers
        )

    @searchable.setter
    def searchable(self, value: bool) -> None:
        self._searchable_override = value

    def add(self, providers: list[Any]) -> None:
        for provider in providers:
            identity = instance_identity(provider)["instance_id"]
            self.providers[:] = [
                member
                for member in self.providers
                if instance_identity(member)["instance_id"] != identity
            ]
            self.providers.append(provider)

    def remove(self, providers: list[Any]) -> None:
        self.providers[:] = [
            member
            for member in self.providers
            if not any(member is provider for provider in providers)
        ]

    async def is_available(self) -> bool:
        async def probe(provider: Any) -> bool:
            try:
                return bool(await provider.is_available())
            except Exception:
                return False

        tasks = [asyncio.create_task(probe(provider)) for provider in self.providers]
        try:
            for result in asyncio.as_completed(tasks):
                if await result:
                    return True
            return False
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _catalog(self, method: str, *args: Any) -> list[LocalModel]:
        from gideon.integrations.local_models.registry import to_local_model

        async def collect(provider: Any) -> list[LocalModel]:
            try:
                raw = await getattr(provider, method)(*args)
                identity = instance_identity(provider)
                return [
                    replace(
                        to_local_model(model),
                        instance_id=identity["instance_id"],
                        instance_label=identity["instance_label"],
                    )
                    for model in raw
                ]
            except Exception:
                logger.warning(
                    "%s failed for %s",
                    method,
                    instance_identity(provider)["instance_id"],
                    exc_info=True,
                )
                return []

        rows = await asyncio.gather(*(collect(provider) for provider in self.providers))
        models: dict[str, LocalModel] = {}
        for catalog in rows:
            for model in catalog:
                previous = models.get(model.name)
                if previous is None:
                    models[model.name] = model
                    continue
                selected = (
                    model if model.downloaded and not previous.downloaded else previous
                )
                models[model.name] = replace(
                    selected,
                    capabilities=list(
                        dict.fromkeys([*previous.capabilities, *model.capabilities])
                    ),
                )
        return list(models.values())

    async def list_models(self) -> list[LocalModel]:
        return await self._catalog("list_models")

    async def search_models(self, query: str) -> list[LocalModel]:
        return await self._catalog("search_models", query)

    async def _write(self, method: str, model_name: str) -> bool:
        results = await asyncio.gather(
            *(getattr(provider, method)(model_name) for provider in self.providers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("%s failed for %s: %s", method, self.name, result)
        return bool(results) and all(result is True for result in results)

    async def download_model(self, model_name: str) -> bool:
        return await self._write("download_model", model_name)

    async def delete_model(self, model_name: str) -> bool:
        return await self._write("delete_model", model_name)

    async def self_test(self, capability: str) -> CapabilitySelfTestResult:
        for provider in self.providers:
            try:
                available = await provider.is_available()
            except Exception:
                available = False
            if available:
                return await provider.self_test(capability)
        return CapabilitySelfTestResult.failed(
            LocalModelFailureCode.UNAVAILABLE,
            f"{self.name} has no available instance",
        )

    def loaded_models(self) -> list[dict[str, Any]]:
        return [
            {**row, **instance_identity(provider)}
            for provider in self.providers
            for row in provider.loaded_models()
        ]

    def unload(self) -> bool:
        results = [provider.unload() for provider in self.providers]
        return any(results)
