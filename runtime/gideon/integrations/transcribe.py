"""Resolve speech input, segment large recordings and assemble safe transcripts."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any

from gideon.core.cancellation import terminate_and_reap
from gideon.integrations.stt.provider import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)

logger = logging.getLogger(__name__)
_STT_SEGMENT_THRESHOLD = 25 * 1024 * 1024
_STT_SEGMENT_SECONDS = 600
_FFMPEG_CANDIDATE_DIRS = [
    os.path.expanduser("~/ffmpeg"),
    os.path.expanduser("~/.local/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]


def ensure_ffmpeg_in_path() -> None:
    original = os.environ.get("PATH", "")
    known = original.split(os.pathsep)
    additions = []
    for directory in reversed(_FFMPEG_CANDIDATE_DIRS):
        if directory not in known and os.path.isfile(os.path.join(directory, "ffmpeg")):
            additions.append(directory)
            known.append(directory)
    if additions:
        os.environ["PATH"] = os.pathsep.join([*reversed(additions), original])


def _ffmpeg_present() -> bool:
    return bool(shutil.which("ffmpeg"))


@dataclass(frozen=True)
class _TranscriptionRequest:
    provider: Any
    model: str
    language: str
    audio_path: str = ""

    async def invoke(self, path: str, *, detailed: bool = False, bias_terms=None):
        arguments = {"model": self.model, "language": self.language}
        if detailed:
            return await self.provider.transcribe_detailed(
                path, **arguments, bias_terms=bias_terms
            )
        return await self.provider.transcribe(path, **arguments)

    def needs_segments(self) -> bool:
        try:
            size = os.path.getsize(self.audio_path)
        except OSError:
            return False
        return size > _stt_segment_threshold() and _ffmpeg_present()


def _select_input(audio_path: str | None = None) -> _TranscriptionRequest | None:
    from gideon.extensions.providers.use_cases import load_use_case_settings
    from gideon.integrations.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not settings.get("enabled", True):
        logger.debug("STT disabled in settings")
        return None
    if audio_path is not None:
        from gideon.security.security import is_sensitive_path

        if is_sensitive_path(audio_path):
            logger.error("Refusing to read sensitive path: %s", audio_path)
            return None
    selection = active_stt()
    if selection is None:
        logger.debug("No active STT model selected")
        return None
    return _TranscriptionRequest(
        selection[0],
        selection[1],
        str(settings.get("language_code", "") or ""),
        audio_path or "",
    )


async def is_available() -> bool:
    request = _select_input()
    if request is None or not await request.provider.is_available():
        return False
    ensure_ffmpeg_in_path()
    if not _ffmpeg_present():
        logger.warning("ffmpeg not found; .webm transcription will be unavailable")
    return True


def _redacted_text(text: str) -> str:
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    for redact in (redact_exfiltration_urls, redact_credentials):
        text, _warnings = redact(text)
    return text


async def _transcription(audio_path: str, *, detailed: bool, bias_terms=None):
    request = _select_input(audio_path)
    if request is None:
        return None
    ensure_ffmpeg_in_path()
    if request.needs_segments():
        if detailed:
            result = await _transcribe_segmented_detailed(
                request.provider,
                request.model,
                request.language,
                audio_path,
                bias_terms,
            )
        else:
            result = await _transcribe_segmented(
                request.provider, request.model, request.language, audio_path
            )
    else:
        result = await request.invoke(
            audio_path, detailed=detailed, bias_terms=bias_terms
        )
    if detailed:
        if result is not None and result.text:
            result.text = _redacted_text(result.text)
    elif result:
        result = _redacted_text(result)
    return result


async def transcribe_audio(audio_path: str) -> str | None:
    return await _transcription(audio_path, detailed=False)


async def transcribe_audio_detailed(
    audio_path: str, *, bias_terms: list[str] | None = None
):
    return await _transcription(audio_path, detailed=True, bias_terms=bias_terms)


def _stt_segment_threshold() -> int:
    try:
        requested = int(os.environ.get("GIDEON_STT_SEGMENT_THRESHOLD", ""))
    except (TypeError, ValueError):
        return _STT_SEGMENT_THRESHOLD
    return requested if requested > 0 else _STT_SEGMENT_THRESHOLD


@dataclass
class _SegmentWorkspace:
    executable: str
    source: str
    seconds: float
    directory: str = field(init=False, default="")

    def __enter__(self):
        self.directory = tempfile.mkdtemp(prefix="stt_seg_")
        return self

    def __exit__(self, *_exception):
        shutil.rmtree(self.directory, ignore_errors=True)

    def command(self) -> tuple[str, ...]:
        return (
            self.executable,
            "-y",
            "-i",
            self.source,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "segment",
            "-segment_time",
            str(self.seconds),
            os.path.join(self.directory, "seg_%05d.wav"),
        )

    def complete(self, returncode: int) -> list[str]:
        chunks = sorted(
            os.path.join(self.directory, name)
            for name in os.listdir(self.directory)
            if name.startswith("seg_")
        )
        if returncode == 0 and chunks:
            return chunks
        logger.warning(
            "STT segmentation failed (rc=%s); using the original recording", returncode
        )
        return []


@dataclass
class _TranscriptAssembly:
    texts: list[str] = field(default_factory=list)
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0

    def append(self, result: TranscriptResult, offset: float) -> None:
        self.language = self.language or result.language
        self.duration = offset + (result.duration or 0.0)
        if result.text:
            self.texts.append(result.text.strip())
        self.segments.extend(
            TranscriptSegment(
                segment.start + offset,
                segment.end + offset,
                segment.text,
                segment.speaker,
                [
                    TranscriptWord(
                        word.start + offset, word.end + offset, word.word, word.prob
                    )
                    for word in segment.words
                ],
            )
            for segment in result.segments
        )

    def finish(self) -> TranscriptResult | None:
        text = " ".join(filter(None, self.texts)).strip()
        if text or self.segments:
            return TranscriptResult(
                text=text,
                language=self.language,
                duration=self.duration,
                segments=self.segments,
            )
        return None


async def _read_chunk(
    request: _TranscriptionRequest, chunk: str, *, detailed=False, bias_terms=None
):
    try:
        return await request.invoke(chunk, detailed=detailed, bias_terms=bias_terms)
    except Exception:
        logger.warning("STT segment failed: %s", os.path.basename(chunk), exc_info=True)
        return None


async def _wait_split(process) -> int:
    try:
        return await process.wait()
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(BaseException):
                await terminate_and_reap(process)
        raise


async def _transcribe_segmented_detailed(
    provider,
    model_id: str,
    language: str,
    audio_path: str,
    bias_terms: list[str] | None,
):
    request = _TranscriptionRequest(provider, model_id, language, audio_path)
    executable = shutil.which("ffmpeg")
    if not executable:
        return await request.invoke(audio_path, detailed=True, bias_terms=bias_terms)
    with _SegmentWorkspace(executable, audio_path, _STT_SEGMENT_SECONDS) as workspace:
        process = await asyncio.create_subprocess_exec(
            *workspace.command(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        chunks = workspace.complete(await _wait_split(process))
        if not chunks:
            return await request.invoke(
                audio_path, detailed=True, bias_terms=bias_terms
            )
        combined = _TranscriptAssembly()
        for position, chunk in enumerate(chunks):
            result = await _read_chunk(
                request, chunk, detailed=True, bias_terms=bias_terms
            )
            if result is not None:
                combined.append(result, position * float(_STT_SEGMENT_SECONDS))
        return combined.finish()


async def _transcribe_segmented(
    provider, model_id: str, language: str, audio_path: str
) -> str | None:
    request = _TranscriptionRequest(provider, model_id, language, audio_path)
    executable = shutil.which("ffmpeg")
    if not executable:
        return await request.invoke(audio_path)
    with _SegmentWorkspace(executable, audio_path, _STT_SEGMENT_SECONDS) as workspace:
        process = await asyncio.create_subprocess_exec(
            *workspace.command(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        chunks = workspace.complete(await _wait_split(process))
        if not chunks:
            return await request.invoke(audio_path)
        logger.info(
            "STT: transcribing %d segments of %s",
            len(chunks),
            os.path.basename(audio_path),
        )
        pieces = []
        for chunk in chunks:
            text = await _read_chunk(request, chunk)
            if text:
                pieces.append(text.strip())
        return " ".join(filter(None, pieces))
