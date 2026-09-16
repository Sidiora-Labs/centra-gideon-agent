"""Voice state exercised with isolated files, real WAV clips and concurrent callers."""

import json
import stat
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.extensions.providers import use_cases
from gideon.integrations.tts import registry as tts
from gideon.integrations.tts.openai_provider import OpenAITtsProvider
from gideon.integrations.voice import bindings, duplex, migration, profiles


@pytest.fixture
def voice_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}')
    return tmp_path


def _audio(path, seconds=1.1):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(1000)
        stream.writeframes(b"\x01\x00" * int(seconds * 1000))
    return path


def test_legacy_records_normalize_fields_without_sharing_record_containers(voice_home):
    record = {
        "id": "older-voice",
        "name": 12,
        "kind": "",
        "speed": "unreadable",
        "seed": None,
        "design_params": {"accent": "local"},
        "history": [{"path": "history/old.wav", "seed": 4}, False, "invalid"],
        "verified_own_voice": True,
        "unknown_future_field": "ignored",
    }
    path = profiles.profile_path("older-voice")
    path.parent.mkdir()
    path.write_text(json.dumps(record))
    restored = profiles.require_profile("older-voice")
    assert (restored.name, restored.kind, restored.speed, restored.seed) == (
        "12",
        "design",
        1.0,
        0,
    )
    assert restored.verified_own_voice is False
    assert len(restored.history) == 1
    payload = restored.to_dict()
    payload["design_params"]["accent"] = "changed"
    payload["history"][0]["seed"] = 88
    assert restored.design_params == {"accent": "local"}
    assert restored.history[0]["seed"] == 4
    assert "unknown_future_field" not in payload


def test_invalid_edit_does_not_publish_partial_mutations(voice_home):
    profile = profiles.create_profile(name="Original", seed=4)
    path = profiles.profile_path(profile.id)
    before = path.read_bytes()
    with pytest.raises(profiles.VoiceProfileError) as error:
        profiles.update_profile(profile.id, name="Not committed", speed={})
    assert (error.value.reason, error.value.status) == ("invalid_speed", 400)
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.glob("*.tmp")) == []


def test_concurrent_generations_keep_bounded_records_and_exact_audio(voice_home):
    profile = profiles.create_profile(name="Concurrent")
    clip = _audio(voice_home / "generation.wav", 0.05)
    count = profiles.HISTORY_MAX * 3
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda seed: profiles.append_history(profile.id, clip, seed=seed),
                range(count),
            )
        )
    stored = profiles.require_profile(profile.id)
    assert len(stored.history) == profiles.HISTORY_MAX
    assert len({entry["seed"] for entry in stored.history}) == profiles.HISTORY_MAX
    files = set(profiles.artifact_path(profile.id, "history").iterdir())
    expected = {
        profiles.artifact_path(profile.id, entry["path"]) for entry in stored.history
    }
    assert files == expected
    assert all(path.read_bytes() == clip.read_bytes() for path in files)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    profiles.lock_profile(profile.id, 0)
    assert (
        profiles.artifact_path(profile.id, "locked.wav").read_bytes()
        == clip.read_bytes()
    )
    assert profiles.require_profile(profile.id).seed == stored.history[0]["seed"]


def test_reference_replacement_moves_input_and_removes_only_previous_reference(
    voice_home,
):
    profile = profiles.create_profile(name="Reference", kind="clone")
    first = voice_home / "one.MP3"
    second = voice_home / "two.wav"
    first.write_bytes(b"first reference")
    second.write_bytes(b"second reference")
    profiles.attach_ref_audio(profile.id, first)
    retained = profiles.artifact_path(profile.id, "other.wav")
    retained.write_bytes(b"keep")
    updated = profiles.attach_ref_audio(profile.id, second)
    assert not first.exists() and not second.exists()
    assert updated.ref_audio == "ref_audio.wav"
    assert not profiles.artifact_path(profile.id, "ref_audio.mp3").exists()
    assert (
        profiles.artifact_path(profile.id, updated.ref_audio).read_bytes()
        == b"second reference"
    )
    assert retained.read_bytes() == b"keep"
    assert profiles.profile_payload(updated)["artifacts"] == {
        "ref_audio": True,
        "consent": False,
        "locked": False,
    }


def test_audio_first_consent_keeps_recorded_time_and_revocation_removes_provenance(
    voice_home,
):
    profile = profiles.create_profile(name="Consent", kind="clone")
    attached = profiles.attach_consent_audio(
        profile.id, _audio(voice_home / "first.wav")
    )
    first_time = attached.consent_recorded_at
    replaced = profiles.attach_consent_audio(
        profile.id, _audio(voice_home / "second.wav")
    )
    assert replaced.consent_recorded_at == first_time
    assert replaced.verified_own_voice is False
    approved = profiles.record_consent(profile.id, consent_text="  This is my voice.  ")
    assert approved.consent_text == "This is my voice."
    assert approved.verified_own_voice is True
    profiles.assert_artifact_release_allowed(approved, "locked")
    revoked = profiles.revoke_consent(profile.id)
    assert not any(
        (
            revoked.consent_text,
            revoked.consent_audio,
            revoked.consent_recorded_at,
            revoked.verified_own_voice,
        )
    )
    assert profiles.consent_recording(profile.id) is None
    with pytest.raises(profiles.VoiceProfileError) as error:
        profiles.assert_artifact_release_allowed(revoked, "locked")
    assert error.value.reason == "consent_required"


def test_recorded_flag_and_linked_consent_never_verify_a_clone(voice_home):
    profile = profiles.create_profile(name="Linked", kind="clone")
    external = _audio(voice_home / "external.wav")
    profiles.artifact_path(profile.id, "consent.wav").symlink_to(external)
    changed = profiles.record_consent(profile.id, consent_text="Consent")
    assert changed.verified_own_voice is False
    assert profiles.consent_recording(profile.id) is None
    profiles.revoke_consent(profile.id)
    assert external.is_file()


def test_consent_duration_precedes_encoded_size_fallback(voice_home):
    profile = profiles.create_profile(name="Audio duration", kind="clone")
    short = _audio(voice_home / "short.wav", 0.5)
    with short.open("ab") as stream:
        stream.write(b"padding" * profiles.MIN_CONSENT_BYTES)
    assert short.stat().st_size > profiles.MIN_CONSENT_BYTES
    denied = profiles.record_consent(
        profile.id, consent_text="Consent", audio_source=short
    )
    assert denied.verified_own_voice is False
    encoded = voice_home / "encoded.mp3"
    encoded.write_bytes(b"x" * profiles.MIN_CONSENT_BYTES)
    approved = profiles.attach_consent_audio(profile.id, encoded)
    assert approved.verified_own_voice is True
    assert not profiles.artifact_path(profile.id, "consent.wav").exists()


def test_missing_history_audio_preserves_current_profile(voice_home):
    profile = profiles.create_profile(name="Missing audio")
    added = profiles.append_history(
        profile.id, _audio(voice_home / "clip.wav"), seed=41
    )
    profiles.artifact_path(profile.id, added.history[0]["path"]).unlink()
    before = profiles.profile_path(profile.id).read_bytes()
    with pytest.raises(profiles.VoiceProfileError) as error:
        profiles.lock_profile(profile.id, 0)
    assert (error.value.status, error.value.reason) == (409, "history_audio_missing")
    assert profiles.profile_path(profile.id).read_bytes() == before


def test_catalog_skips_unreadable_records_and_orders_stable_timestamps(voice_home):
    first = profiles.create_profile(name="First")
    second = profiles.create_profile(name="Second")
    root = profiles.profiles_root()
    (root / "broken.json").write_text("{broken")
    (root / "array.json").write_text("[]")
    (root / "no-id.json").write_text('{"name": "no id"}')
    assert [item.id for item in profiles.list_profiles()] == [second.id, first.id]


def test_concurrent_bindings_persist_every_surface_and_forget_only_target(voice_home):
    chosen = profiles.create_profile(name="Chosen")
    fallback = profiles.create_profile(name="Fallback")
    bindings.set_binding("default", fallback.id)
    surfaces = [f"client:device-{index}" for index in range(32)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(lambda surface: bindings.set_binding(surface, chosen.id), surfaces)
        )
    assert bindings.load_bindings() == {
        "default": fallback.id,
        **dict.fromkeys(surfaces, chosen.id),
    }
    assert stat.S_IMODE(bindings.bindings_path().stat().st_mode) == 0o600
    profiles.delete_profile(chosen.id)
    assert bindings.resolve_profile_id(surface=surfaces[0]) == (fallback.id, "default")
    assert bindings.forget_profile(chosen.id) == {"default": fallback.id}


def test_binding_decode_retains_valid_keys_and_resolution_does_not_write(voice_home):
    profile = profiles.create_profile(name="Bound")
    bindings.bindings_path().write_text(
        json.dumps(
            {
                "default": profile.id,
                " channel:a ": profile.id,
                "agent:bad": [],
                "other:key": profile.id,
            }
        )
    )
    before = bindings.bindings_path().read_bytes()
    assert bindings.load_bindings() == {
        "default": profile.id,
        " channel:a ": profile.id,
    }
    assert bindings.resolve_profile_id(surface="channel:a") == (profile.id, "default")
    assert bindings.bindings_path().read_bytes() == before


def test_migration_captures_real_flat_config_despite_existing_profile_binding(
    voice_home, monkeypatch
):
    monkeypatch.setattr(tts, "_providers", {})
    monkeypatch.setattr(tts, "_remote_names", set())
    account = "voice-state-account"
    (voice_home / "config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": account,
                        "type": "openai",
                        "model": "",
                        "options": {"api_key": "", "endpoint": "http://localhost:1/v1"},
                    }
                ]
            }
        )
    )
    tts.register_provider(OpenAITtsProvider(provider_name=account))
    use_cases.save_active_models({"tts": [account + ":speech:revision"]})
    use_cases.save_use_case_settings("tts", {"speed": "1.25", "speech_voice": "nova"})
    previous = profiles.create_profile(name="Previous", model="do-not-recapture")
    bindings.set_binding("default", previous.id)
    migrated = migration.migrate_active_to_default_profile(name="  Captured  ")
    assert (migrated.name, migrated.provider, migrated.model, migrated.speed) == (
        "Captured",
        account,
        "speech:revision",
        1.25,
    )
    assert migrated.design_params == {"speech_voice": "nova"}
    assert migrated.kind == "design" and migrated.verified_own_voice is False
    assert bindings.resolve_profile_id() == (migrated.id, "default")
    assert profiles.require_profile(previous.id).model == "do-not-recapture"


@pytest.mark.parametrize("width", [1, 2, 3, 4, 5])
def test_echo_requires_contiguous_runs_in_both_directions(width):
    words = "alpha bravo charlie delta echo".split()
    phrase = " ".join(words[:width])
    spoken = "prefix " + phrase + " suffix"
    assert duplex.is_echo(phrase, spoken, min_run=width)
    assert duplex.is_echo(spoken, phrase, min_run=width)
    assert not duplex.is_echo(phrase, spoken, min_run=width + 1)


def test_tail_decisions_keep_apostrophes_and_expand_for_long_phrases():
    phrase = "don't send until I say so"
    assert duplex.is_confirmation("ready, " + phrase, [phrase], tail_words=1)
    assert not duplex.is_confirmation(
        phrase + " wait for several extra words after", [phrase], tail_words=1
    )
    assert duplex.is_exit("I'm finished", {"I'm finished"}, tail_words=2)
    assert not duplex.is_exit("Im finished", {"I'm finished"}, tail_words=2)


def test_speech_projection_orders_fence_link_url_and_path_cleanup():
    source = "# Result\r\nRead [the guide](https://hidden.example/a) and ftp://user@www.example.org:22/path.\r~~~\rsecret /tmp/path --erase\r~~~\rOpen ~/docs/note.txt --quiet, now!!"
    assert (
        duplex.clean_for_speech(source)
        == "Result Read the guide and example.org code block. Open note.txt, now!"
    )
