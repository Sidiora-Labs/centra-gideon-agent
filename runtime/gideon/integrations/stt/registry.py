"""Track configured speech adapters and resolve active transcription models."""

from dataclasses import dataclass
from threading import RLock
from typing import Any

from gideon.integrations.generation_catalog import resolve_selection
from gideon.integrations.stt.provider import SttProvider

_providers: dict[str, SttProvider] = {}
_remote_names: set[str] = set()
_catalog_lock = RLock()
_FASTER_WHISPER_NAMES = ("faster-whisper", "faster_whisper")
_ALIASES = dict.fromkeys(_FASTER_WHISPER_NAMES, "faster_whisper")


def register_provider(provider: SttProvider) -> None:
    with _catalog_lock:
        _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    with _catalog_lock:
        _providers.pop(name, None)
        _remote_names.discard(name)


def get_provider(name: str) -> SttProvider | None:
    with _catalog_lock:
        return _providers.get(name)


def list_providers() -> list[SttProvider]:
    with _catalog_lock:
        return [*_providers.values()]


@dataclass(frozen=True)
class _SpeechCandidate:
    name: str
    settings: dict | None = None
    instance: Any = None

    def publish(self) -> None:
        from gideon.integrations.stt.openai_provider import OpenAISttProvider

        with _catalog_lock:
            if self.settings is None:
                adapter = self.instance
            elif self.name in _providers:
                return
            else:
                options = {key: self.settings[key] for key in ("endpoint", "api_key")}
                options.update(
                    provider_name=self.name, provider_type=self.settings.get("type", "")
                )
                adapter = OpenAISttProvider(**options)
            _providers[self.name] = adapter
            _remote_names.add(self.name)


def _remote_candidates():
    from gideon.extensions.providers.media_scanners import scan
    from gideon.extensions.providers.use_cases import openai_family_providers

    for entry in openai_family_providers():
        yield _SpeechCandidate(entry["name"], settings=entry)
    for adapter in scan("stt"):
        name = getattr(adapter, "name", "")
        if name:
            yield _SpeechCandidate(name, instance=adapter)


def _register_remote_providers() -> None:
    for candidate in _remote_candidates():
        candidate.publish()


def _ensure_registered() -> None:
    _register_remote_providers()


def refresh_providers() -> None:
    with _catalog_lock:
        for name in _remote_names.intersection(_providers):
            del _providers[name]
        _remote_names.clear()


def active_stt() -> tuple[SttProvider, str] | None:
    return resolve_selection(
        "stt", _ensure_registered, lambda name: get_provider(_ALIASES.get(name, name))
    )


def get_active_provider() -> SttProvider | None:
    selection = active_stt()
    return selection[0] if selection is not None else None
