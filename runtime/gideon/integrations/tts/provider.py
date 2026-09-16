"""Speech inference contracts and the local voice-to-model adapter."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from gideon.integrations.local_models.provider import LocalModel, LocalModelProvider


@dataclass
class TtsVoice:
    name: str
    language: str = ""
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False


def _voice_model(voice: TtsVoice) -> LocalModel:
    names = ("name", "size_mb", "description", "downloaded")
    metadata = {name: getattr(voice, name) for name in names}
    return LocalModel(**metadata, capabilities=["tts"])


class TtsProvider(ABC):
    supports_cloning: bool = False
    supports_voice_design: bool = False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "",
        output_path: str = "",
        *,
        speed: float = 1.0,
        **opts: Any,
    ) -> str | None: ...

    async def can_synthesize(self, voice: str = "") -> bool:
        return await self.is_available()

    def info(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in ("name", "display_name")}


class LocalTtsProvider(TtsProvider, LocalModelProvider):
    @abstractmethod
    async def list_voices(self) -> list[TtsVoice]: ...

    @abstractmethod
    async def download_voice(self, voice_name: str) -> bool: ...

    @abstractmethod
    async def delete_voice(self, voice_name: str) -> bool: ...

    async def list_models(self) -> list[LocalModel]:
        voices = await self.list_voices()
        return list(map(_voice_model, voices))

    async def download_model(self, model_name: str) -> bool:
        return await self.download_voice(model_name)

    async def delete_model(self, model_name: str) -> bool:
        return await self.delete_voice(model_name)
