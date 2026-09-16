"""TTS configuration, profile projection and binary output without remote inference."""

import asyncio
import json
from pathlib import Path

import pytest

from gideon.extensions.providers import media_scanners, use_cases
from gideon.integrations.media_catalogs import (
    MediaCatalog,
    MediaModel,
    register_media_catalog,
    unregister_media_catalogs,
)
from gideon.integrations.tts import registry
from gideon.integrations.tts.audio_output import (
    AudioDestination,
    response_audio,
    speech_payload,
)
from gideon.integrations.tts.openai_provider import OpenAITtsProvider
from gideon.integrations.tts.provider import TtsVoice, _voice_model
from gideon.integrations.voice import bindings, profiles


@pytest.fixture
def speech_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(registry, "_remote_names", set())
    monkeypatch.setattr(media_scanners, "_scanners", {})
    (tmp_path / "config.json").write_text('{"providers": []}')
    return tmp_path


def _configured(home, *names):
    providers = [
        {
            "name": name,
            "type": "openai",
            "model": "",
            "options": {"endpoint": "http://localhost:1/v1", "api_key": ""},
        }
        for name in names
    ]
    (home / "config.json").write_text(json.dumps({"providers": providers}))


def test_config_discovery_preserves_manual_adapters_and_refreshes_owned_entries(
    speech_home,
):
    manual = OpenAITtsProvider(provider_name="manual")
    registry.register_provider(manual)
    _configured(speech_home, "manual", "configured")
    use_cases.save_active_models({"tts": ["configured:model:version"]})
    provider, voice = registry.active_tts()
    assert voice == "model:version"
    assert isinstance(provider, OpenAITtsProvider)
    assert provider.name == "configured"
    assert registry.get_provider("manual") is manual
    assert registry._remote_names == {"configured"}
    snapshot = registry.list_providers()
    registry.refresh_providers()
    assert registry.list_providers() == [manual]
    assert snapshot == [manual, provider]
    replacement, _ = registry.active_tts()
    assert replacement is not provider
    registry.unregister_provider("configured")
    assert "configured" not in registry._remote_names
    registry.unregister_provider("absent")


def test_authoritative_adapter_candidate_replaces_family_adapter(speech_home):
    first = OpenAITtsProvider(provider_name="account", endpoint="http://localhost:1/v1")
    replacement = OpenAITtsProvider(
        provider_name="account", endpoint="http://localhost:2/v1"
    )
    registry.register_provider(first)
    registry._AdapterCandidate("account", configuration={"name": "account"}).publish()
    assert registry.get_provider("account") is first
    assert registry._remote_names == set()
    registry._AdapterCandidate("account", instance=replacement).publish()
    assert registry.get_provider("account") is replacement
    assert registry._remote_names == {"account"}
    registry.refresh_providers()
    assert registry.get_provider("account") is None


def test_first_selection_and_piper_aliases_preserve_voice_identifiers(speech_home):
    _configured(speech_home, "piper", "piper-tts")
    config_path = speech_home / "config.json"
    config = json.loads(config_path.read_text())
    config["providers"].append({"name": "missing", "type": "ollama", "model": "local"})
    config_path.write_text(json.dumps(config))
    provider = OpenAITtsProvider(provider_name="piper")
    registry.register_provider(provider)
    for alias in ("piper", "piper-tts"):
        use_cases.save_active_models({"tts": [f"{alias}:voice:edition"]})
        assert registry.active_tts() == (provider, "voice:edition")
        assert registry.get_active_provider() is provider
        assert registry._provider_by_app_name(alias) is provider
    use_cases.save_active_models({"tts": ["unqualified", "piper:later"]})
    assert registry.active_tts() is None
    use_cases.save_active_models({"tts": ["missing:voice", "piper:later"]})
    assert registry.active_tts() is None
    assert registry._provider_by_app_name("") is None


def test_flat_parameters_read_real_settings_and_keep_exact_shape(speech_home):
    _configured(speech_home, "account")
    use_cases.save_active_models({"tts": ["account:model"]})
    use_cases.save_use_case_settings(
        "tts", {"speed": "bad", "enabled": 1, "auto_speak": 0, "speech_voice": None}
    )
    params = registry.active_voice_params()
    assert list(params) == [
        "provider",
        "voice",
        "speed",
        "speech_voice",
        "enabled",
        "auto_speak",
    ]
    assert params["provider"].name == "account"
    assert params["voice"] == "model"
    assert params["speed"] == 1.0
    assert params["speech_voice"] == ""
    assert params["enabled"] is True
    assert params["auto_speak"] is False


def test_profile_can_select_provider_without_flat_binding(speech_home):
    _configured(speech_home, "profile-account")
    profile = profiles.create_profile(
        name="Voice",
        provider="profile-account",
        model="speech-v2",
        speed=1.3,
        seed=41,
        ref_text="reference",
        instruct="quiet",
        design_params={"style": "calm"},
    )
    bindings.set_binding("default", profile.id)
    params = registry.active_voice_params()
    assert params["provider"].name == "profile-account"
    assert params["voice"] == "speech-v2"
    assert params["speed"] == 1.3
    assert params["seed"] == 41
    assert params["profile_id"] == profile.id
    assert params["profile_level"] == "default"
    assert params["ref_audio"] == ""
    params["design_params"]["style"] = "changed in caller"
    assert profiles.get_profile(profile.id).design_params == {"style": "calm"}


def test_unknown_profile_engine_uses_flat_provider_but_profile_model(speech_home):
    _configured(speech_home, "flat-account")
    use_cases.save_active_models({"tts": ["flat-account:flat-model"]})
    use_cases.save_use_case_settings("tts", {"speed": 1.7})
    profile = profiles.create_profile(
        name="Unavailable engine", provider="absent", model="profile-model", speed=0
    )
    params = registry.active_voice_params(profile_id=profile.id)
    assert params["provider"].name == "flat-account"
    assert params["voice"] == "profile-model"
    assert params["speed"] == 1.7
    use_cases.save_active_models({})
    assert registry.active_voice_params(profile_id=profile.id) is None


def test_reference_audio_requires_a_contained_existing_file(speech_home):
    profile = profiles.create_profile(
        name="Reference", provider="account", kind="clone"
    )
    profile.ref_audio = "reference.wav"
    assert registry._reference_audio(profile) == ""
    reference = profiles.artifact_path(profile.id, profile.ref_audio)
    reference.write_bytes(b"local reference bytes")
    assert registry._reference_audio(profile) == str(reference)
    profile.locked = True
    assert registry._reference_audio(profile) == ""
    locked = profiles.artifact_path(profile.id, "locked.wav")
    locked.write_bytes(b"locked local bytes")
    assert registry._reference_audio(profile) == str(locked)
    profile.locked = False
    profile.ref_audio = "../outside.wav"
    assert registry._reference_audio(profile) == ""


def test_local_voice_projection_retains_download_metadata_only():
    voice = TtsVoice(
        "voice",
        language="en",
        size_mb=42.5,
        description="English voice",
        downloaded=True,
        active=True,
    )
    model = _voice_model(voice)
    assert model.name == voice.name
    assert model.size_mb == 42.5
    assert model.description == "English voice"
    assert model.downloaded is True
    assert model.capabilities == ["tts"]
    assert model.matrix is None
    assert model.runtime == ""


def test_model_defaults_come_from_the_declared_vendor_catalog(speech_home):
    provider_type = "tts-local-catalog-contract"
    provider = OpenAITtsProvider(provider_name="account", provider_type=provider_type)
    assert provider._request("hello", "", "", 1.0) is None
    register_media_catalog(
        "tts",
        provider_type,
        MediaCatalog((MediaModel("vendor-voice"),), "vendor-voice"),
    )
    try:
        assert provider._request("hello", "", "", 1.0) == {
            "model": "vendor-voice",
            "voice": "alloy",
            "input": "hello",
        }
        assert provider._request("hello", "explicit", "nova", 1.2) == {
            "model": "explicit",
            "voice": "nova",
            "input": "hello",
            "speed": 1.2,
        }
        assert provider._request(" \n", "explicit", "", 1.0) is None
    finally:
        unregister_media_catalogs(provider_type)


@pytest.mark.parametrize(
    "speed, included", [(0, False), (1.0, False), (1.4, True), (-1, True)]
)
def test_speech_payload_retains_speed_omission_contract(speed, included):
    payload = speech_payload(" text ", "model", "persona", speed)
    assert payload["input"] == " text "
    assert ("speed" in payload) is included
    if included:
        assert payload["speed"] == speed


@pytest.mark.asyncio
async def test_real_sync_and_async_binary_readers_preserve_audio_bytes(tmp_path):
    source = tmp_path / "audio.bin"
    content = bytes(range(256))
    source.write_bytes(content)
    with source.open("rb") as response:
        assert await response_audio(response) == content
    stream = asyncio.StreamReader()
    stream.feed_data(source.read_bytes())
    stream.feed_eof()
    assert await response_audio(stream) == content
    assert await response_audio(content) == b""


def test_output_ownership_controls_cleanup_and_preserves_empty_body_behavior(tmp_path):
    supplied = tmp_path / "chosen.mp3"
    supplied.write_bytes(b"original")
    caller = AudioDestination.allocate(str(supplied))
    assert caller.owned is False
    assert caller.write(b"") is None
    assert supplied.read_bytes() == b"original"
    assert caller.write(b"new bytes") == str(supplied)
    caller.discard()
    assert supplied.read_bytes() == b"new bytes"
    owned = AudioDestination.allocate("")
    try:
        assert owned.owned is True and owned.path.endswith(".mp3")
        assert Path(owned.path).is_file()
        assert owned.write(b"") is None
        assert Path(owned.path).is_file()
        assert owned.write(b"audio bytes") == owned.path
        assert Path(owned.path).read_bytes() == b"audio bytes"
    finally:
        OpenAITtsProvider._cleanup(owned.path, "")
    assert not Path(owned.path).exists()
    OpenAITtsProvider._cleanup(str(supplied), str(supplied))
    assert supplied.is_file()


@pytest.mark.asyncio
async def test_unconfigured_remote_is_unavailable_without_touching_output(speech_home):
    provider = OpenAITtsProvider(provider_name="account")
    assert await provider.is_available() is False
    assert await provider.can_synthesize("model") is False
    path = speech_home / "unchanged.mp3"
    path.write_bytes(b"existing")
    assert (
        await provider.synthesize("hello", voice="model", output_path=str(path)) is None
    )
    assert path.read_bytes() == b"existing"
    assert provider.info() == {
        "name": "account",
        "display_name": "account (remote TTS)",
    }
    assert provider.supports_cloning is False
    assert provider.supports_voice_design is False


def test_cloning_gate_precedes_option_conversion_and_audio_work(speech_home):
    provider = OpenAITtsProvider(provider_name="account")
    with pytest.raises(registry.CloningUnsupportedError) as error:
        registry.guard_synthesis_capability(
            provider, {"ref_audio": "local.wav", "speed": "invalid"}
        )
    assert error.value.status == 409
    assert str(error.value) == "cloning_unsupported:account"
    assert registry._synthesis_arguments({"speed": 0, "seed": "7", "voice": None}) == {
        "voice": "",
        "speed": 1.0,
        "speech_voice": "",
        "ref_audio": "",
        "ref_text": "",
        "seed": 7,
        "instruct": "",
        "design_params": {},
    }
