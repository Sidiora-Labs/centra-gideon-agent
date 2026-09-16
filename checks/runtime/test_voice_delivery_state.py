"""Voice delivery using real codecs, filesystem ownership and persisted preferences."""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

import pytest

from gideon.extensions.providers import use_cases
from gideon.integrations import natural_voice, transcribe, voice_reply
from gideon.integrations.prompt_providers.base import PromptSnippet
from gideon.integrations.prompt_providers.native_provider import NativePromptProvider
from gideon.integrations.stt import registry as stt_registry
from gideon.integrations.stt.openai_provider import OpenAISttProvider
from gideon.integrations.stt.provider import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from gideon.integrations.tts.openai_provider import OpenAITtsProvider


@pytest.fixture
def delivery_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "config.json").write_text('{"providers": []}')
    return tmp_path


@pytest.fixture
def codec():
    executable = shutil.which("ffmpeg")
    assert executable, "real ffmpeg is required for the selected voice delivery gate"
    return executable


def _wav(path, sample=100, frames=3200):
    data = sample.to_bytes(2, "little", signed=True) * frames
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(data)
    return data


def _read_wav(path):
    with wave.open(str(path), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (
            1,
            2,
            16000,
        )
        return audio.readframes(audio.getnframes())


@pytest.mark.asyncio
async def test_real_segmentation_keeps_all_samples_and_removes_workspace(
    delivery_home, codec
):
    source = delivery_home / "recording.wav"
    data = _wav(source, frames=16000)
    with transcribe._SegmentWorkspace(codec, str(source), 0.2) as workspace:
        directory = Path(workspace.directory)
        process = await asyncio.create_subprocess_exec(
            *workspace.command(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        chunks = workspace.complete(await process.wait())
        assert len(chunks) >= 2
        assert b"".join(_read_wav(chunk) for chunk in chunks) == data
        assert directory.is_dir()
    assert not directory.exists()
    assert _read_wav(source) == data


@pytest.mark.asyncio
async def test_real_segmentation_failure_and_exception_release_files(
    delivery_home, codec
):
    with transcribe._SegmentWorkspace(
        codec, str(delivery_home / "absent.wav"), 0.2
    ) as workspace:
        process = await asyncio.create_subprocess_exec(
            *workspace.command(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert workspace.complete(await process.wait()) == []
        directory = Path(workspace.directory)
    assert not directory.exists()
    with pytest.raises(RuntimeError):
        with transcribe._SegmentWorkspace(codec, "unused", 1) as workspace:
            directory = Path(workspace.directory)
            (directory / "partial.wav").write_bytes(b"partial")
            raise RuntimeError("consumer failed")
    assert not directory.exists()


def test_timeline_offsets_copy_all_metadata_and_preserve_input_objects():
    word = TranscriptWord(0.1, 0.8, "hello", 0.73)
    segment = TranscriptSegment(0.0, 1.0, "hello", "speaker-a", [word])
    result = TranscriptResult(
        " hello ", language="de", duration=1.5, segments=[segment]
    )
    combined = transcribe._TranscriptAssembly()
    combined.append(result, 0.0)
    combined.append(TranscriptResult(" ", language="fr", duration=0), 600.0)
    combined.append(result, 1200.0)
    merged = combined.finish()
    assert merged.text == "hello hello"
    assert merged.language == "de" and merged.duration == 1201.5
    assert merged.segments[1].speaker == "speaker-a"
    assert (merged.segments[1].start, merged.segments[1].end) == (1200.0, 1201.0)
    assert (merged.segments[1].words[0].start, merged.segments[1].words[0].end) == (
        1200.1,
        1200.8,
    )
    assert merged.segments[1].words[0].prob == 0.73
    assert word.start == 0.1 and segment.start == 0.0
    merged.segments[0].words[0].word = "changed"
    assert word.word == "hello"


def test_timeline_retains_timed_segments_without_flat_text():
    result = transcribe._TranscriptAssembly()
    assert result.finish() is None
    result.append(
        TranscriptResult(" ", segments=[TranscriptSegment(0, 1, "segment only")]), 2
    )
    assert result.finish().text == ""
    assert result.finish().segments[0].text == "segment only"
    assert result.finish().duration == 2


@pytest.mark.parametrize(
    "setting,expected",
    [
        ("", 26214400),
        ("0", 26214400),
        ("-1", 26214400),
        ("invalid", 26214400),
        ("42", 42),
    ],
)
def test_segment_threshold_uses_only_positive_overrides(monkeypatch, setting, expected):
    monkeypatch.setenv("GIDEON_STT_SEGMENT_THRESHOLD", setting)
    assert transcribe._stt_segment_threshold() == expected


def test_ffmpeg_path_discovery_preserves_priority_and_avoids_duplicates(
    delivery_home, codec, monkeypatch
):
    first, second = delivery_home / "first", delivery_home / "second"
    for directory in (first, second):
        directory.mkdir()
        (directory / "ffmpeg").symlink_to(codec)
    monkeypatch.setenv("PATH", "/existing")
    monkeypatch.setattr(
        transcribe, "_FFMPEG_CANDIDATE_DIRS", [str(first), str(second), str(first)]
    )
    transcribe.ensure_ffmpeg_in_path()
    expected = ":".join((str(second), str(first), "/existing"))
    assert os.environ["PATH"] == expected
    transcribe.ensure_ffmpeg_in_path()
    assert os.environ["PATH"] == expected


@pytest.mark.asyncio
async def test_real_selection_handles_disabled_unavailable_and_sensitive_inputs(
    delivery_home, monkeypatch
):
    monkeypatch.setattr(stt_registry, "_providers", {})
    monkeypatch.setattr(stt_registry, "_remote_names", set())
    provider = OpenAISttProvider(provider_name="delivery-account")
    (delivery_home / "config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": provider.name,
                        "type": "openai",
                        "model": "",
                        "options": {"api_key": ""},
                    }
                ]
            }
        )
    )
    stt_registry.register_provider(provider)
    use_cases.save_active_models({"stt": [provider.name + ":speech:version"]})
    use_cases.save_use_case_settings("stt", {"enabled": True, "language_code": "en-GB"})
    clip = delivery_home / "audio.wav"
    _wav(clip)
    selected = transcribe._select_input(str(clip))
    assert selected.provider is provider
    assert (selected.model, selected.language) == ("speech:version", "en-GB")
    assert await transcribe.is_available() is False
    assert await transcribe.transcribe_audio(str(clip)) is None
    assert (
        await transcribe.transcribe_audio_detailed(str(clip), bias_terms=["Gideon"])
        is None
    )
    assert transcribe._select_input(str(delivery_home / "session_key")) is None
    use_cases.save_use_case_settings("stt", {"enabled": False})
    assert await transcribe.transcribe_audio(str(clip)) is None
    assert await transcribe.transcribe_audio_detailed(str(clip)) is None


@pytest.mark.asyncio
async def test_segmented_unavailable_provider_degrades_without_model_calls(
    delivery_home, codec, monkeypatch
):
    source = delivery_home / "long.wav"
    _wav(source, frames=8000)
    monkeypatch.setattr(transcribe, "_STT_SEGMENT_SECONDS", 0.2)
    monkeypatch.setattr(tempfile, "tempdir", str(delivery_home))
    provider = OpenAISttProvider(provider_name="unconfigured")
    assert (
        await transcribe._transcribe_segmented(provider, "model", "", str(source)) == ""
    )
    assert (
        await transcribe._transcribe_segmented_detailed(
            provider, "model", "", str(source), []
        )
        is None
    )
    assert not list(delivery_home.glob("stt_seg_*"))


@pytest.mark.asyncio
async def test_real_stitch_preserves_every_clip_and_caller_sources(
    delivery_home, codec
):
    first = delivery_home / "one 'quoted'.wav"
    second = delivery_home / "two | pipe.wav"
    first_data, second_data = _wav(first, 100), _wav(second, -100)
    output = delivery_home / "combined.wav"
    result = await voice_reply.stitch_wavs([str(first), str(second)], str(output))
    assert result == str(output)
    assert _read_wav(result) == first_data + second_data
    assert _read_wav(first) == first_data and _read_wav(second) == second_data


@pytest.mark.asyncio
async def test_stitch_temporary_result_ownership_and_failure_cleanup(
    delivery_home, codec, monkeypatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(delivery_home))
    source = delivery_home / "source.wav"
    payload = _wav(source)
    result = await voice_reply.stitch_wavs([str(source), str(source)])
    assert result is not None
    assert _read_wav(result) == payload + payload
    Path(result).unlink()
    before = set(delivery_home.iterdir())
    assert (
        await voice_reply.stitch_wavs([str(source), str(delivery_home / "missing.wav")])
        is None
    )
    assert set(delivery_home.iterdir()) == before
    assert await voice_reply.stitch_wavs([]) is None
    assert await voice_reply.stitch_wavs([str(source)]) == str(source)
    copy = delivery_home / "copy.wav"
    assert await voice_reply.stitch_wavs([str(source)], str(copy)) == str(copy)
    assert copy.read_bytes() == source.read_bytes()


@pytest.mark.asyncio
async def test_cancelled_split_wait_reaps_real_child():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    waiter = asyncio.create_task(transcribe._wait_split(process))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert process.returncode is not None


def test_audio_lease_removes_files_when_reading_or_delivery_fails(delivery_home):
    clip = delivery_home / "lease.wav"
    clip.write_bytes(b"audio")
    with pytest.raises(RuntimeError):
        with voice_reply._AudioLease(str(clip)) as lease:
            assert lease.read() == b"audio"
            raise RuntimeError("delivery failed")
    assert not clip.exists()
    with pytest.raises(FileNotFoundError):
        with voice_reply._AudioLease(str(clip)) as lease:
            lease.read()


@pytest.mark.parametrize(
    "path,filename",
    [
        ("/tmp/clip.mp3", "voice-reply.mp3"),
        ("/tmp/clip", "voice-reply.wav"),
        ("/tmp/.hidden", "voice-reply.wav"),
        ("/tmp/clip.OGG", "voice-reply.OGG"),
    ],
)
def test_channel_payload_preserves_format_and_thread(path, filename):
    assert voice_reply._ChannelAudio("channel", "thread", path).payload() == {
        "channel": "channel",
        "thread_ts": "thread",
        "file": path,
        "filename": filename,
        "title": "🔊 Voice Reply",
    }


def test_channel_projection_preserves_spoken_structure_and_redacts_credentials():
    text = "```diff\n+ changed\n```\n| Name | Value |\n| --- | --- |\n| A | B |\n`/private/file` [OPTIONS: yes, no] 🙂 <https://example.org|Guide>"
    projected = voice_reply.strip_markdown(text)
    assert "(diff block)" in projected
    assert "(table with 2 rows)" in projected
    assert "(file path)" in projected and "Guide" in projected
    assert "OPTIONS" not in projected and "🙂" not in projected
    secret = "AKIAIOSFODNN7EXAMPLE"
    assert secret not in voice_reply._speech_text(secret, "local test")
    assert secret not in transcribe._redacted_text(secret)


@pytest.mark.asyncio
async def test_unconfigured_tts_keeps_reply_outputs_empty(delivery_home):
    provider = OpenAITtsProvider(provider_name="unconfigured")
    assert await voice_reply.synthesize_speech(provider, " ") is None
    assert (
        await voice_reply.synthesize_speech(provider, "Hello.", voice="speech") is None
    )
    assert [
        item
        async for item in voice_reply.streaming_voice_reply(
            provider, "First. Second.", voice="speech"
        )
    ] == []


def test_natural_voice_instruction_cache_preserves_edits_until_explicit_clear(
    delivery_home,
):
    provider = NativePromptProvider()
    provider.create_snippet(
        PromptSnippet(name="natural-voice", content="First local instruction.")
    )
    natural_voice.instruction.cache_clear()
    try:
        assert (
            natural_voice.maybe_inject("Original", "on")
            == "Original\n\nFirst local instruction.\n"
        )
        provider.update_snippet(
            "natural-voice",
            PromptSnippet(name="natural-voice", content="Updated local instruction."),
        )
        assert natural_voice.instruction() == "First local instruction."
        natural_voice.instruction.cache_clear()
        assert natural_voice.instruction() == "Updated local instruction."
        assert natural_voice.maybe_inject("Original", "off", agent=True) == "Original"
    finally:
        natural_voice.instruction.cache_clear()


def test_natural_voice_agent_preference_reads_real_config_without_mutation(
    delivery_home,
):
    config = delivery_home / "config.json"
    config.write_text(
        json.dumps(
            {
                "agents": {
                    "plain": {"natural_voice": True},
                    "default": {"natural_voice": False},
                }
            }
        )
    )
    before = config.read_bytes()
    assert natural_voice.agent_default(" plain ") is True
    assert natural_voice.agent_default("default") is False
    assert natural_voice.agent_default("absent") is False
    assert natural_voice.agent_default("") is False
    assert config.read_bytes() == before


def test_natural_voice_resolution_uses_declared_order(monkeypatch):
    monkeypatch.setattr(
        natural_voice,
        "NATURAL_VOICE_PRECEDENCE",
        ("unknown", "agent", "conversation", "platform"),
    )
    assert natural_voice.resolve("off", True) == natural_voice.NaturalVoice(
        True, "agent"
    )
    assert natural_voice.resolve("off", False) == natural_voice.NaturalVoice(
        False, "conversation"
    )
    monkeypatch.setattr(natural_voice, "NATURAL_VOICE_PRECEDENCE", ("unknown",))
    with pytest.raises(AssertionError):
        natural_voice.resolve("on", True)
