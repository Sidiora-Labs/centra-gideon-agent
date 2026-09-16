"""Speech request payloads, response bytes and output-file ownership."""

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from typing import Any


def speech_payload(text: str, model: str, persona: str, speed: float) -> dict[str, Any]:
    values = dict(zip(("model", "voice", "input"), (model, persona, text)))
    if speed and speed != 1.0:
        values["speed"] = speed
    return values


async def response_audio(response: Any) -> Any:
    if hasattr(response, "read"):
        content = response.read()
    else:
        content = getattr(response, "content", b"")
    return await content if asyncio.iscoroutine(content) else content


@dataclass(frozen=True)
class AudioDestination:
    path: str
    owned: bool

    @classmethod
    def allocate(cls, requested: str) -> AudioDestination:
        if requested:
            return cls(requested, False)
        descriptor, location = tempfile.mkstemp(suffix=".mp3")
        os.close(descriptor)
        return cls(location, True)

    def write(self, audio: Any) -> str | None:
        if not audio:
            return None
        with open(self.path, "wb") as destination:
            destination.write(audio)
        return self.path

    def discard(self) -> None:
        if self.path and self.owned:
            with suppress(OSError):
                os.unlink(self.path)


@dataclass(frozen=True)
class SpeechExchange:
    api_key: str
    endpoint: str
    request: dict[str, Any]
    destination: AudioDestination

    async def execute(self, client_factory: Any) -> str | None:
        client = client_factory(api_key=self.api_key, base_url=self.endpoint or None)
        try:
            response = await client.audio.speech.create(**self.request)
            return self.destination.write(await response_audio(response))
        finally:
            with suppress(Exception):
                await client.close()
