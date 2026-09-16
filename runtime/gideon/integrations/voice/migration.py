"""Explicitly capture the flat TTS selection as a default design profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gideon.integrations.voice import bindings as vb
from gideon.integrations.voice import profiles as vp

DEFAULT_MIGRATED_NAME = "My current voice"


@dataclass(frozen=True)
class _VoiceCapture:
    provider: str
    model: str
    speed: float
    persona: str

    @classmethod
    def read(cls) -> _VoiceCapture | None:
        from gideon.extensions.providers.use_cases import load_use_case_settings
        from gideon.integrations.tts.registry import active_tts

        selection = active_tts()
        if selection is None:
            return None
        settings = load_use_case_settings("tts")
        try:
            speed = float(settings.get("speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        return cls(
            provider=str(getattr(selection[0], "name", "") or ""),
            model=str(selection[1] or ""),
            speed=speed,
            persona=str(settings.get("speech_voice", "") or ""),
        )

    def fields(self) -> dict[str, Any]:
        result = {
            "kind": "design",
            "provider": self.provider,
            "model": self.model,
            "speed": self.speed,
        }
        if self.persona:
            result["design_params"] = {"speech_voice": self.persona}
        return result


def active_voice_fields() -> dict[str, Any] | None:
    capture = _VoiceCapture.read()
    return capture.fields() if capture is not None else None


def migrate_active_to_default_profile(*, name: str = "") -> vp.VoiceProfile:
    source = active_voice_fields()
    if source is None:
        raise vp.VoiceProfileError(
            "no active TTS voice to migrate — bind one in Settings → Models first",
            409,
            "no_active_voice",
        )
    requested = source | {"name": str(name or "").strip() or DEFAULT_MIGRATED_NAME}
    created = vp.create_profile(**requested)
    vb.set_binding(vb.DEFAULT_KEY, created.id)
    return created
