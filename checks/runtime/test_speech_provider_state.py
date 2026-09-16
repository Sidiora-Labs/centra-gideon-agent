"""Speech contracts, config selection and actual audio-file ownership without inference."""

import asyncio
import json
import wave
from dataclasses import asdict

import pytest
from aiohttp import web

from gideon.extensions.providers import media_scanners, use_cases
from gideon.integrations.diarization import registry as diarization
from gideon.integrations.diarization.provider import (
    DiarizationModel,
    DiarizationProvider,
    SpeakerTurn,
)
from gideon.integrations.media_catalogs import (
    MediaCatalog,
    MediaModel,
    register_media_catalog,
    unregister_media_catalogs,
)
from gideon.integrations.stt import registry
from gideon.integrations.stt.handlers import register_stt_routes
from gideon.integrations.stt.openai_provider import OpenAISttProvider
from gideon.integrations.stt.provider import (
    SttModel,
    SttProvider,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from gideon.integrations.stt.transcription_runtime import (
    TranscriptionJob,
    bounded_transcription,
    transcript_text,
)


@pytest.fixture
def speech_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(registry, "_remote_names", set())
    monkeypatch.setattr(diarization, "_providers", {})
    monkeypatch.setattr(media_scanners, "_scanners", {})
    (tmp_path / "config.json").write_text('{"providers": []}')
    return tmp_path


def _config(home, *names):
    entries = [
        {
            "name": name,
            "type": "openai",
            "model": "",
            "options": {"api_key": "", "endpoint": "http://localhost:1/v1"},
        }
        for name in names
    ]
    (home / "config.json").write_text(json.dumps({"providers": entries}))


def _wave_file(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 320)
    return path


def test_rich_transcript_serialization_preserves_nested_fields_and_detaches_values(
    tmp_path,
):
    word = TranscriptWord(0.0, 0.5, "hello", prob=0)
    segment = TranscriptSegment(0.0, 1.0, "hello", speaker="SPEAKER_00", words=[word])
    transcript = TranscriptResult("  hello  ", "en", 1.0, [segment])
    output = transcript.to_dict()
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(output))
    assert json.loads(path.read_text()) == {
        "text": "  hello  ",
        "language": "en",
        "duration": 1.0,
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "hello",
                "speaker": "SPEAKER_00",
                "words": [{"start": 0.0, "end": 0.5, "word": "hello", "prob": 0}],
            }
        ],
    }
    output["segments"][0]["words"][0]["word"] = "caller mutation"
    assert word.word == "hello"
    assert TranscriptResult("").segments is not TranscriptResult("").segments
    assert TranscriptSegment(0, 1, "").words is not TranscriptSegment(0, 1, "").words


@pytest.mark.parametrize(
    "text, expected",
    [
        (" \nrecognized words\t", "recognized words"),
        ("", None),
        (" \t", None),
        ("déjà vu", "déjà vu"),
    ],
)
def test_response_text_normalization_uses_real_transcript_records(text, expected):
    assert transcript_text(TranscriptResult(text=text)) == expected
    assert transcript_text(SttModel(name="metadata has no decoded text")) is None


@pytest.mark.parametrize(
    "language, expected",
    [
        ("en-US", "en"),
        ("zh-Hant-TW", "zh"),
        ("en_US", "en_US"),
        ("", None),
        ("-en", None),
    ],
)
def test_transcription_job_reads_real_wav_and_maps_language(
    tmp_path, language, expected
):
    source = _wave_file(tmp_path / "input.wav")
    job = TranscriptionJob(str(source), "model:version", language, "", "")
    with job.arguments() as arguments:
        handle = arguments["file"]
        assert handle.read() == source.read_bytes()
        assert arguments["model"] == "model:version"
        assert arguments.get("language") == expected
        assert ("language" in arguments) is (expected is not None)
    assert handle.closed
    assert source.is_file()


def test_upload_file_closes_when_local_request_preparation_fails(tmp_path):
    source = _wave_file(tmp_path / "input.wav")
    job = TranscriptionJob(str(source), "model", "en", "", "")
    with pytest.raises(FileNotFoundError):
        with job.arguments() as arguments:
            handle = arguments["file"]
            (tmp_path / "missing-side-input").read_bytes()
    assert handle.closed
    with pytest.raises(FileNotFoundError):
        with TranscriptionJob(
            str(tmp_path / "missing.wav"), "m", "", "", ""
        ).arguments():
            pass


def test_registry_config_refresh_preserves_explicit_adapters(speech_home):
    _config(speech_home, "manual", "account")
    manual = OpenAISttProvider(provider_name="manual")
    registry.register_provider(manual)
    use_cases.save_active_models({"stt": ["account:model:edition"]})
    provider, model = registry.active_stt()
    assert model == "model:edition"
    assert isinstance(provider, OpenAISttProvider)
    assert registry.get_active_provider() is provider
    assert registry.get_provider("manual") is manual
    assert registry._remote_names == {"account"}
    snapshot = registry.list_providers()
    registry.refresh_providers()
    assert registry.list_providers() == [manual]
    assert snapshot == [manual, provider]
    replacement, _ = registry.active_stt()
    assert replacement is not provider
    registry.unregister_provider("account")
    assert registry._remote_names == set()
    registry.unregister_provider("missing")


def test_config_candidate_skips_existing_and_contributed_candidate_overrides(
    speech_home,
):
    original = OpenAISttProvider(provider_name="account")
    override = OpenAISttProvider(
        provider_name="account", endpoint="http://localhost:2/v1"
    )
    registry.register_provider(original)
    registry._SpeechCandidate("account", settings={"name": "account"}).publish()
    assert registry.get_provider("account") is original
    assert registry._remote_names == set()
    registry._SpeechCandidate("account", instance=override).publish()
    assert registry.get_provider("account") is override
    assert registry._remote_names == {"account"}
    registry.refresh_providers()
    assert registry.get_provider("account") is None


def test_aliases_and_first_binding_use_real_configuration(speech_home):
    _config(speech_home, "faster-whisper", "faster_whisper")
    provider = OpenAISttProvider(provider_name="faster_whisper")
    registry.register_provider(provider)
    for name in ("faster-whisper", "faster_whisper"):
        use_cases.save_active_models({"stt": [f"{name}:voice:revision"]})
        assert registry.active_stt() == (provider, "voice:revision")
    use_cases.save_active_models({"stt": ["unqualified", "faster-whisper:later"]})
    assert registry.active_stt() is None
    config_path = speech_home / "config.json"
    config = json.loads(config_path.read_text())
    config["providers"].append({"name": "not-stt", "type": "ollama", "model": "local"})
    config_path.write_text(json.dumps(config))
    use_cases.save_active_models({"stt": ["not-stt:first", "faster-whisper:later"]})
    assert registry.active_stt() is None


def test_job_selection_uses_contributed_default_and_explicit_model(speech_home):
    provider_type = "local-stt-catalog-contract"
    provider = OpenAISttProvider(provider_name="account", provider_type=provider_type)
    assert provider._job("unused.wav", "", "en-US", "") is None
    register_media_catalog(
        "stt", provider_type, MediaCatalog((MediaModel("transcriber"),), "transcriber")
    )
    try:
        selected = provider._job("unused.wav", "", "en-US", "local-setting")
        assert selected.model == "transcriber"
        assert selected.language == "en"
        assert selected.api_key == "local-setting"
        assert provider._job("unused.wav", "pinned", "", "").model == "pinned"
    finally:
        unregister_media_catalogs(provider_type)


def test_configured_credentials_take_priority_over_environment(
    speech_home, monkeypatch
):
    provider = OpenAISttProvider(provider_name="configured", api_key="config-value")
    fallback = OpenAISttProvider(provider_name="environment")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-value")
    assert provider._resolve_api_key() == "config-value"
    assert fallback._resolve_api_key() == "environment-value"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert fallback._resolve_api_key() == ""


@pytest.mark.asyncio
async def test_bounded_transcription_handles_real_file_io_timeout_and_cancellation(
    tmp_path,
):
    source = tmp_path / "decoded.txt"
    source.write_text("decoded text")
    assert (
        await bounded_transcription(asyncio.to_thread(source.read_text), "local")
        == "decoded text"
    )
    assert (
        await bounded_transcription(
            asyncio.to_thread((tmp_path / "absent").read_text), "local"
        )
        is None
    )
    assert await bounded_transcription(asyncio.sleep(1), "local", timeout=0.01) is None
    task = asyncio.create_task(bounded_transcription(asyncio.sleep(1), "local"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_unconfigured_provider_keeps_audio_and_detailed_contract(speech_home):
    path = _wave_file(speech_home / "input.wav")
    before = path.read_bytes()
    provider = OpenAISttProvider(provider_name="unconfigured")
    assert await provider.is_available() is False
    assert await provider.transcribe(str(path), model="pinned") is None
    assert (
        await provider.transcribe_detailed(
            str(path), model="pinned", bias_terms=["Gideon"]
        )
        is None
    )
    assert path.read_bytes() == before
    assert provider.info() == {
        "name": "unconfigured",
        "display_name": "unconfigured (remote STT)",
        "supports_streaming": False,
    }
    assert provider.supports_segments is False
    assert provider.supports_word_timestamps is False
    assert provider.supports_bias_terms is False


@pytest.mark.asyncio
async def test_diarization_empty_state_and_declared_wire_types(speech_home):
    _config(speech_home, "account")
    use_cases.save_active_models({"diarization": ["account:local-model"]})
    assert diarization.active_diarization() is None
    assert diarization.get_active_provider() is None
    assert diarization.get_provider("account") is None
    assert diarization.list_providers() == []
    assert await diarization.list_all_providers_info() == []
    diarization.unregister_provider("absent")
    turn = SpeakerTurn(0.0, 2.5, "SPEAKER_01")
    assert asdict(turn) == {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_01"}
    model = DiarizationModel("local", gated=True, languages=["en"])
    assert asdict(model) == {
        "name": "local",
        "size_mb": 0,
        "description": "",
        "downloaded": False,
        "active": False,
        "gated": True,
        "languages": ["en"],
    }
    with pytest.raises(TypeError):
        DiarizationProvider()
    with pytest.raises(TypeError):
        SttProvider()


def test_legacy_stt_registration_hook_does_not_add_duplicate_routes():
    application = web.Application()
    before = list(application.router.routes())
    assert register_stt_routes(application) is None
    assert list(application.router.routes()) == before
