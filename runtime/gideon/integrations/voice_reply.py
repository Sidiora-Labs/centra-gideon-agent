"""Prepare spoken replies and transfer provider-owned audio to channel surfaces."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.core.cancellation import run_with_timeout, terminate_and_reap
from gideon.integrations.tts.provider import TtsProvider
from gideon.security.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)
DEFAULT_RATE = "100%"
MAX_CHARS = 2900
_RATE_RE = re.compile(r"^\d{1,3}%$")


def _validate_rate(rate: str) -> str:
    if _RATE_RE.match(rate) is None:
        return DEFAULT_RATE
    return rate


def _fenced_audio_label(match: re.Match) -> str:
    label = "diff" if match.group(0).startswith("```diff") else "code"
    return f" ({label} block) "


def _table_audio_label(match: re.Match) -> str:
    lines = (
        line for line in match.group(0).splitlines() if line.strip().startswith("|")
    )
    count = sum(1 for _line in lines) - 1
    return f" (table with {count} rows) "


def _inline_audio_label(match: re.Match) -> str:
    text = match.group(1)
    return text if len(text) <= 30 and "/" not in text else " (file path) "


_CHANNEL_PROJECTION = (
    (re.compile(r"```[\s\S]*?```"), _fenced_audio_label),
    (re.compile(r"(?:^\|.+\|$\n?){2,}", re.MULTILINE), _table_audio_label),
    (re.compile(r"`([^`]+)`"), _inline_audio_label),
    (re.compile(r"<([^|>]+)\|([^>]+)>"), r"\2"),
    (re.compile(r"<https?://[^>]+>"), " (link) "),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"[*_~]+"), ""),
    (re.compile(r":[a-z0-9_+-]+:"), ""),
    (
        re.compile(
            r"[\U0001f300-\U0001faff\U00002702-\U000027b0\U0000fe00-\U0000fe0f\U0000200d]+"
        ),
        "",
    ),
    (re.compile(r"https?://\S+"), " (link) "),
    (re.compile(r"\[OPTIONS:.*?\]"), ""),
    (re.compile(r"^@@[^@]+@@.*$", re.MULTILINE), ""),
    (re.compile(r"\n{3,}"), "\n\n"),
    (re.compile(r"  +"), " "),
)


def strip_markdown(text: str) -> str:
    for pattern, replacement in _CHANNEL_PROJECTION:
        text = pattern.sub(replacement, text)
    return text.strip()


def _speech_text(text: str, origin: str) -> str:
    filters = (
        (redact_credentials, "credential pattern(s)"),
        (redact_exfiltration_urls, "suspicious URL(s)"),
    )
    for redact, description in filters:
        text, warnings = redact(text)
        if warnings:
            logger.warning(
                "%s: redacted %d %s before TTS", origin, len(warnings), description
            )
    return text


@dataclass(frozen=True)
class _SpeechStyle:
    voice: str = ""
    speed: float = 1.0
    speech_voice: str = ""

    def options(self) -> dict[str, Any]:
        return {
            "voice": self.voice,
            "speed": self.speed,
            "speech_voice": self.speech_voice,
        }


@dataclass(frozen=True)
class _AudioLease:
    path: str

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        with contextlib.suppress(OSError):
            os.unlink(self.path)

    def read(self) -> bytes:
        return Path(self.path).read_bytes()


@dataclass(frozen=True)
class _ChannelAudio:
    channel: str
    thread: str
    path: str

    def payload(self) -> dict[str, str]:
        extension = os.path.splitext(self.path)[1].lstrip(".") or "wav"
        return {
            "channel": self.channel,
            "thread_ts": self.thread,
            "file": self.path,
            "filename": "voice-reply." + extension,
            "title": "🔊 Voice Reply",
        }


async def _synthesize_chunk(
    provider: TtsProvider, text: str, *, voice: str, speed: float, speech_voice: str
) -> str | None:
    style = _SpeechStyle(voice, speed, speech_voice)
    return await provider.synthesize(text, **style.options())


async def synthesize_speech(
    provider: TtsProvider,
    text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    speech_voice: str = "",
) -> str | None:
    prepared = strip_markdown(_speech_text(text, "voice_reply")).strip()
    if prepared:
        return await _synthesize_chunk(
            provider, prepared, **_SpeechStyle(voice, speed, speech_voice).options()
        )
    return None


async def upload_voice_to_channel(
    channel_client: Any, channel: str, thread_ts: str, audio_path: str
) -> bool:
    attachment = _ChannelAudio(channel, thread_ts, audio_path)
    try:
        await channel_client.upload_file(**attachment.payload())
    except Exception:
        logger.exception("Channel file upload failed")
        return False
    return True


def split_sentences(text: str) -> list[str]:
    clean = strip_markdown(text)
    sentences = map(str.strip, re.split(r"(?<=[.!?])\s+", clean))
    return list(filter(None, sentences))


@dataclass
class _StitchTarget:
    path: str
    allocated: bool
    completed: bool = False

    @classmethod
    def create(cls, output: str | None):
        if output is not None:
            return cls(output, False)
        descriptor, path = tempfile.mkstemp(suffix=".wav")
        os.close(descriptor)
        return cls(path, True)

    def finish(self, returncode: int) -> str | None:
        self.completed = returncode == 0 and os.path.exists(self.path)
        return self.path if self.completed else None

    def close(self) -> None:
        if self.allocated and not self.completed:
            with contextlib.suppress(OSError):
                os.unlink(self.path)


async def _stop_codec(process) -> None:
    if process is not None and process.returncode is None:
        await terminate_and_reap(process)


@contextlib.contextmanager
def _concat_manifest(paths: list[str]):
    descriptor, manifest = tempfile.mkstemp(prefix="voice_concat_", suffix=".ffconcat")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("ffconcat version 1.0\n")
            for path in paths:
                escaped = os.path.abspath(path).replace("'", "'\\''")
                stream.write("file '" + escaped + "'\n")
        yield manifest
    finally:
        with contextlib.suppress(OSError):
            os.unlink(manifest)


async def stitch_wavs(paths: list[str], output: str | None = None) -> str | None:
    if len(paths) < 2:
        if not paths:
            return None
        if output:
            shutil.copy2(paths[0], output)
        return output or paths[0]
    target = _StitchTarget.create(output)
    process = None
    try:
        if not all(os.path.isfile(path) for path in paths):
            return None
        with _concat_manifest(paths) as manifest:
            try:
                process = await asyncio.create_subprocess_exec(
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    manifest,
                    "-c",
                    "copy",
                    target.path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await run_with_timeout(process, 30)
                return target.finish(process.returncode)
            finally:
                await _stop_codec(process)
    except Exception:
        logger.exception("ffmpeg stitch failed")
        return None
    finally:
        target.close()


async def streaming_voice_reply(
    provider: TtsProvider,
    response_text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    speech_voice: str = "",
):
    sentences = split_sentences(_speech_text(response_text, "stream_voice_chunks"))
    options = _SpeechStyle(voice, speed, speech_voice).options()
    for index, sentence in enumerate(sentences):
        path = await _synthesize_chunk(provider, sentence, **options)
        if path:
            with _AudioLease(path) as audio:
                yield index, sentence, audio.read()


async def voice_reply(
    channel_client: Any,
    channel: str,
    thread_ts: str,
    response_text: str,
    *,
    provider: TtsProvider,
    voice: str = "",
    speed: float = 1.0,
    speech_voice: str = "",
) -> bool:
    options = _SpeechStyle(voice, speed, speech_voice).options()
    path = await synthesize_speech(provider, response_text, **options)
    if path:
        with _AudioLease(path):
            return await upload_voice_to_channel(
                channel_client, channel, thread_ts, path
            )
    return False
