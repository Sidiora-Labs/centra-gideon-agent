"""Hosted speech synthesis through an OpenAI-compatible audio endpoint."""

import asyncio
import importlib
import logging
import os
from typing import Any

from gideon.integrations.tts.audio_output import (
    AudioDestination,
    SpeechExchange,
    speech_payload,
)
from gideon.integrations.tts.provider import TtsProvider

logger = logging.getLogger(__name__)

SPEECH_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_DEFAULT_SPEECH_VOICE = "alloy"


class OpenAITtsProvider(TtsProvider):
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
        return f"{self._provider_name} (remote TTS)"

    def _default_model(self) -> str:
        from gideon.integrations.media_catalogs import get_media_catalog

        catalog = get_media_catalog("tts", self._provider_type)
        return getattr(catalog, "default_model", "")

    async def is_available(self) -> bool:
        if self._resolve_api_key():
            try:
                importlib.import_module("openai")
            except ImportError:
                pass
            else:
                return True
        return False

    def _request(
        self, text: str, voice: str, speech_voice: str, speed: float
    ) -> dict[str, Any] | None:
        if not text.strip():
            return None
        selected = voice or self._default_model()
        if selected:
            return speech_payload(
                text, selected, speech_voice or _DEFAULT_SPEECH_VOICE, speed
            )
        logger.error(
            "No TTS model selected for %r (this endpoint has no contributed "
            "default); pin one in Settings → Models.",
            self._provider_name,
        )
        return None

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        output_path: str = "",
        *,
        speech_voice: str = "",
        speed: float = 1.0,
        **opts: Any,
    ) -> str | None:
        try:
            sdk = importlib.import_module("openai")
        except ImportError:
            logger.error(
                "openai SDK not installed — cannot use remote TTS. Install it with "
                "`pip install 'gideon[openai]'` (or reinstall the OpenAI provider "
                "app); run `gideon doctor` to check provider deps."
            )
            return None
        credential = self._resolve_api_key()
        if not credential:
            logger.error("No API key for remote TTS provider %r", self._provider_name)
            return None
        request = self._request(text, voice, speech_voice, speed)
        if request is None:
            return None
        destination = AudioDestination.allocate(output_path)
        exchange = SpeechExchange(credential, self._endpoint, request, destination)
        try:
            return await asyncio.wait_for(
                exchange.execute(sdk.AsyncOpenAI), timeout=120
            )
        except asyncio.TimeoutError:
            logger.error("Remote TTS timed out for provider %r", self._provider_name)
        except Exception:
            logger.exception("Remote TTS failed for provider %r", self._provider_name)
        self._cleanup(destination.path, output_path)
        return None

    @staticmethod
    def _cleanup(path: str, output_path: str) -> None:
        AudioDestination(path, not bool(output_path)).discard()

    def _resolve_api_key(self) -> str:
        return self._api_key or os.environ.get("OPENAI_API_KEY", "")
