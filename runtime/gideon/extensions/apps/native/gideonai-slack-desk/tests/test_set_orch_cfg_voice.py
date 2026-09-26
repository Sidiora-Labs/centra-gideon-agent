"""Tests for set_orch_cfg voice behavior restore.

Voice behavior toggles (enabled / auto_speak / auto_reply_to_voice) load from
``use_case_settings/tts.json`` — the unified store. The voice model itself is the
``tts`` selection in active_models.json (resolved per-reply, not here).
"""

from types import SimpleNamespace

import pytest

from slack_desk_runtime import handler as handler_mod
from slack_desk_runtime.handler import _vc, set_orch_cfg


@pytest.fixture(autouse=True)
def _reset_vc():
    _vc.auto_speak = False
    _vc.global_enabled = False
    _vc.auto_reply_to_voice = False
    yield
    _vc.auto_speak = False
    _vc.global_enabled = False
    _vc.auto_reply_to_voice = False


def _tts_settings(monkeypatch, settings: dict) -> None:
    """Point load_use_case_settings('tts') at *settings*.

    The handler resolves it through the channel SDK facade
    (gideon.sdk.channel.load_use_case_settings), so patch it THERE — that is
    the name set_orch_cfg looks up."""
    import gideon.sdk.channel as ch

    monkeypatch.setattr(
        ch, "load_use_case_settings",
        lambda use_case: dict(settings) if use_case == "tts" else {},
    )


def test_set_orch_cfg_restores_auto_speak_true(monkeypatch):
    _tts_settings(monkeypatch, {"enabled": True, "auto_speak": True})
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_speak is True
    assert _vc.global_enabled is True


def test_set_orch_cfg_auto_speak_defaults_false_when_missing(monkeypatch):
    _tts_settings(monkeypatch, {"enabled": True})  # no auto_speak key
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_speak is False


# ── auto_reply_to_voice default follows enabled ─────────────────────────


def test_auto_reply_to_voice_defaults_false_when_enabled_false(monkeypatch):
    """Explicit ``enabled=false`` users keep zero-voice behavior."""
    _tts_settings(monkeypatch, {"enabled": False})
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_reply_to_voice is False
    assert _vc.global_enabled is False


def test_auto_reply_to_voice_defaults_true_when_enabled_true(monkeypatch):
    """Globally-enabled users automatically get symmetric voice-in/voice-out."""
    _tts_settings(monkeypatch, {"enabled": True})
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_reply_to_voice is True
    assert _vc.global_enabled is True


def test_auto_reply_to_voice_explicit_overrides_enabled_false(monkeypatch):
    """User can set ``auto_reply_to_voice=true`` while keeping ``enabled=false``."""
    _tts_settings(monkeypatch, {"enabled": False, "auto_reply_to_voice": True})
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_reply_to_voice is True
    assert _vc.global_enabled is False


def test_auto_reply_to_voice_explicit_overrides_enabled_true(monkeypatch):
    """User can set ``auto_reply_to_voice=false`` while keeping ``enabled=true``."""
    _tts_settings(monkeypatch, {"enabled": True, "auto_reply_to_voice": False})
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_reply_to_voice is False
    assert _vc.global_enabled is True


def test_auto_reply_to_voice_default_when_no_settings(monkeypatch):
    """No tts settings at all -> both default to False (no surprise voice)."""
    _tts_settings(monkeypatch, {})
    set_orch_cfg(SimpleNamespace())
    assert _vc.auto_reply_to_voice is False
    assert _vc.global_enabled is False
