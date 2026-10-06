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
    SttError,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)

logger = logging.getLogger(__name__)
_STT_SEGMENT_THRESHOLD = 25 * 1024 * 1024
_STT_SEGMENT_SECONDS = 600


class _HomePath(os.PathLike[str]):
    """Resolve an optional user-home executable directory at call time."""

    def __init__(self, relative: str) -> None:
        self._relative = relative

    def __fspath__(self) -> str:
        return os.path.expanduser(self._relative)


_FFMPEG_CANDIDATE_DIRS = [
    _HomePath("~/ffmpeg"),
    _HomePath("~/.local/bin"),
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


async def audio_seconds(path: str) -> float | None:
    """Read actual recording length, using WAV frames or a bounded format probe."""
    import re
    import wave

    if not os.path.isfile(path):
        return None

    def wav_seconds():
        try:
            with wave.open(path, "rb") as clip:
                return clip.getnframes() / clip.getframerate() if clip.getframerate() else None
        except (OSError, EOFError, wave.Error):
            return None

    duration = await asyncio.to_thread(wav_seconds)
    if duration is not None:
        return duration
    executable = shutil.which("ffmpeg")
    if not executable:
        return None
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            executable, "-hide_banner", "-nostdin", "-i", path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, error = await asyncio.wait_for(proc.communicate(), timeout=15)
        found = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", error.decode(errors="replace"))
        if found:
            hours, minutes, seconds = found.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        if proc is not None and proc.returncode is None:
            await terminate_and_reap(proc)
    return None


@dataclass(frozen=True)
class _TranscriptionRequest:
    provider: Any
    model: str
    language: str
    audio_path: str = ""

    async def invoke(self, path: str, *, detailed: bool = False, bias_terms=None):
        from gideon.security.guardrails.media_call import MediaCall, metered_media_call
        from gideon.security.guardrails.failure import BudgetExceededError

        arguments = {"model": self.model, "language": self.language}
        seconds = await audio_seconds(path)
        quantity = seconds / 60 if seconds is not None else None

        async def run():
            if detailed:
                result = await self.provider.transcribe_detailed(
                    path, **arguments, bias_terms=bias_terms
                )
            else:
                result = await self.provider.transcribe(path, **arguments)
            return _require_transcript(result, detailed=detailed)

        def billed(result):
            duration = float(getattr(result, "duration", 0) or 0)
            # For flat transcripts the input's measured frames/header are the actual audio sent.
            return duration / 60 if duration > 0 else quantity

        try:
            return await metered_media_call(
                MediaCall(self.provider.name, self.model, "minute", quantity), run, billed=billed)
        except BudgetExceededError as exc:
            raise SttError("budget_exceeded", detail=exc.sentence()) from exc
        except SttError as exc:
            raise SttError(exc.code, detail=exc.detail) from None
        except Exception:
            raise SttError("provider_failed") from None

    def needs_segments(self) -> bool:
        try:
            size = os.path.getsize(self.audio_path)
        except OSError:
            return False
        return size > _stt_segment_threshold() and _ffmpeg_present()


def _select_input(audio_path: str | None = None) -> _TranscriptionRequest:
    from gideon.extensions.providers.use_cases import load_use_case_settings
    from gideon.integrations.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not settings.get("enabled", True):
        raise SttError("disabled")
    if audio_path is not None:
        from gideon.security.security import is_sensitive_path

        if is_sensitive_path(audio_path):
            raise SttError("sensitive_path")
    selection = active_stt()
    if selection is None:
        raise SttError("no_model")
    return _TranscriptionRequest(
        selection[0],
        selection[1],
        str(settings.get("language_code", "") or ""),
        audio_path or "",
    )


async def is_available() -> bool:
    try:
        request = _select_input()
        if not await request.provider.is_available():
            return False
    except Exception:
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


def _require_transcript(result, *, detailed: bool):
    if result is None:
        raise SttError("no_transcript")
    if detailed:
        valid = isinstance(result, TranscriptResult) and isinstance(result.text, str)
    else:
        valid = isinstance(result, str)
    if not valid:
        raise SttError("provider_failed")
    return result


async def _transcription(audio_path: str, *, detailed: bool, bias_terms=None):
    try:
        request = _select_input(audio_path)
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
        result = _require_transcript(result, detailed=detailed)
        if detailed:
            result.text = _redacted_text(result.text)
            for segment in result.segments:
                segment.text = _redacted_text(segment.text)
                for word in segment.words:
                    word.word = _redacted_text(word.word)
        else:
            result = _redacted_text(result)
        return result
    except SttError as exc:
        raise SttError(exc.code, detail=exc.detail) from None
    except Exception:
        raise SttError("provider_failed") from None


async def transcribe_audio(audio_path: str) -> str:
    try:
        from gideon.cognition.lexicon import get_lexicon_service

        lexicon = get_lexicon_service()
        bias_terms = lexicon.select_bias_terms()
    except Exception:
        logger.warning("Dictation lexicon unavailable; transcribing without it")
        return await _transcription(audio_path, detailed=False)
    result = await _transcription(audio_path, detailed=True, bias_terms=bias_terms)
    try:
        corrections = lexicon.correct(result)
        lexicon.write_dictation_corrections(corrections)
    except Exception:
        logger.warning("Dictation corrections unavailable")
    return _redacted_text(result.text)


async def transcribe_audio_detailed(
    audio_path: str, *, bias_terms: list[str] | None = None
) -> TranscriptResult:
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

    def finish(self) -> TranscriptResult:
        return TranscriptResult(
            text=" ".join(filter(None, self.texts)).strip(),
            language=self.language,
            duration=self.duration,
            segments=self.segments,
        )


async def _read_chunk(
    request: _TranscriptionRequest, chunk: str, *, detailed=False, bias_terms=None
):
    return await request.invoke(chunk, detailed=detailed, bias_terms=bias_terms)


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
        logger.info("STT: transcribing %d segments", len(chunks))
        pieces = []
        for chunk in chunks:
            text = await _read_chunk(request, chunk)
            if text:
                pieces.append(text.strip())
        return " ".join(filter(None, pieces))
