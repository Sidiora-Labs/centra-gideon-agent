"""OpenAI-compatible transcription with catalog-selected models."""

import importlib
import logging
import os

from gideon.integrations.stt.provider import SttProvider
from gideon.integrations.stt.transcription_runtime import (
    TranscriptionJob,
    bounded_transcription,
)

logger = logging.getLogger(__name__)


class OpenAISttProvider(SttProvider):
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
        return f"{self._provider_name} (remote STT)"

    def _default_model(self) -> str:
        from gideon.integrations.media_catalogs import get_media_catalog

        catalog = get_media_catalog("stt", self._provider_type)
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

    def _job(self, audio_path: str, model: str, language: str, credential: str):
        selected = model or self._default_model()
        if selected:
            primary_language = language.split("-")[0] if language else ""
            return TranscriptionJob(
                audio_path, selected, primary_language, credential, self._endpoint
            )
        logger.error(
            "No STT model selected for %r (this endpoint has no contributed "
            "default); pin one in Settings → Models.",
            self._provider_name,
        )
        return None

    async def transcribe(
        self, audio_path: str, model: str = "", language: str = ""
    ) -> str | None:
        try:
            sdk = importlib.import_module("openai")
        except ImportError:
            logger.error(
                "openai SDK not installed — cannot use remote STT. Install it with "
                "`pip install 'gideon[openai]'` (or reinstall the OpenAI provider "
                "app); run `gideon doctor` to check provider deps."
            )
            return None
        credential = self._resolve_api_key()
        if not credential:
            logger.error("No API key for remote STT provider %r", self._provider_name)
            return None
        job = self._job(audio_path, model, language, credential)
        if job is None:
            return None
        return await bounded_transcription(
            job.execute(sdk), self._provider_name, log=logger
        )

    def _resolve_api_key(self) -> str:
        return self._api_key or os.environ.get("OPENAI_API_KEY", "")
