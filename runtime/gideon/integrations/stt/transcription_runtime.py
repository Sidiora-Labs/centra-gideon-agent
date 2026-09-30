"""Transcription payloads, owned audio uploads and bounded request execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any

from gideon.integrations.stt.provider import SttError

logger = logging.getLogger(__name__)


def transcript_text(response: Any) -> str | None:
    value = getattr(response, "text", None)
    if isinstance(value, str):
        return value.strip()
    return None


@dataclass(frozen=True)
class TranscriptionJob:
    path: str
    model: str
    language: str
    api_key: str
    endpoint: str

    @contextmanager
    def arguments(self):
        with open(self.path, "rb") as audio:
            fields = {"model": self.model, "file": audio}
            primary_language = self.language.partition("-")[0] if self.language else ""
            if primary_language:
                fields["language"] = primary_language
            yield fields

    async def execute(self, sdk: Any) -> str | None:
        client = sdk.AsyncOpenAI(api_key=self.api_key, base_url=self.endpoint or None)
        try:
            with self.arguments() as arguments:
                response = await client.audio.transcriptions.create(**arguments)
            return transcript_text(response)
        finally:
            with suppress(Exception):
                await client.close()


async def bounded_transcription(
    operation: Awaitable[str | None],
    provider_name: str,
    timeout: float = 300,
    *,
    log: logging.Logger = logger,
) -> str | None:
    try:
        return await asyncio.wait_for(operation, timeout=timeout)
    except SttError as exc:
        raise SttError(exc.code) from None
    except asyncio.TimeoutError:
        log.error("Remote STT timed out")
        raise SttError("provider_failed") from None
    except Exception:
        log.error("Remote STT failed")
        raise SttError("provider_failed") from None
