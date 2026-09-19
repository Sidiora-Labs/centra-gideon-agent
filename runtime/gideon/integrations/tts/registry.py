"""Resolve speech engines, voice profiles and synthesis options."""

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping

from gideon.integrations.tts.provider import TtsProvider

_providers: dict[str, TtsProvider] = {}
_remote_names: set[str] = set()
_catalog_lock = RLock()
_PIPER_NAMES = ("piper-tts", "piper")
_PROVIDER_ALIASES = dict.fromkeys(_PIPER_NAMES, "piper")


def register_provider(provider: TtsProvider) -> None:
    with _catalog_lock:
        _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    with _catalog_lock:
        _providers.pop(name, None)
        _remote_names.discard(name)


def get_provider(name: str) -> TtsProvider | None:
    with _catalog_lock:
        return _providers.get(name)


def list_providers() -> list[TtsProvider]:
    with _catalog_lock:
        return [*_providers.values()]


def _ensure_registered() -> None:
    _register_remote_providers()


@dataclass(frozen=True)
class _AdapterCandidate:
    name: str
    configuration: Mapping[str, str] | None = None
    instance: Any = None

    def publish(self) -> None:
        from gideon.integrations.tts.openai_provider import OpenAITtsProvider

        with _catalog_lock:
            if self.configuration is None:
                adapter = self.instance
            elif self.name in _providers:
                return
            else:
                entry = self.configuration
                adapter = OpenAITtsProvider(
                    provider_name=self.name,
                    provider_type=entry.get("type", ""),
                    endpoint=entry["endpoint"],
                    api_key=entry["api_key"],
                )
            _providers[self.name] = adapter
            _remote_names.add(self.name)


def _discovery_candidates():
    from gideon.extensions.providers.media_scanners import scan
    from gideon.extensions.providers.use_cases import openai_family_providers

    for entry in openai_family_providers():
        yield _AdapterCandidate(entry["name"], configuration=entry)
    for adapter in scan("tts"):
        name = getattr(adapter, "name", "")
        if name:
            yield _AdapterCandidate(name, instance=adapter)


def _register_remote_providers() -> None:
    for candidate in _discovery_candidates():
        candidate.publish()


def refresh_providers() -> None:
    with _catalog_lock:
        survivors = {
            name: value
            for name, value in _providers.items()
            if name not in _remote_names
        }
        _providers.clear()
        _providers.update(survivors)
        _remote_names.clear()


def _registered_name(name: str) -> str:
    return _PROVIDER_ALIASES.get(name, name)


def active_tts() -> tuple[TtsProvider, str] | None:
    from gideon.extensions.providers.use_cases import active_model_refs, split_ref

    first = next(iter(active_model_refs("tts")), "")
    selection = split_ref(first) if first else None
    if selection is None:
        return None
    name, voice = selection
    _ensure_registered()
    adapter = get_provider(_registered_name(name))
    return None if adapter is None else (adapter, voice)


def get_active_provider() -> TtsProvider | None:
    selected = active_tts()
    return None if selected is None else selected[0]


def _provider_by_app_name(name: str) -> TtsProvider | None:
    if name:
        _ensure_registered()
        return get_provider(_registered_name(name))
    return None


@dataclass(frozen=True)
class _VoiceSelection:
    provider: TtsProvider
    voice: str

    @classmethod
    def resolve(cls, flat: tuple[TtsProvider, str] | None, profile: Any):
        adapter = (
            _provider_by_app_name(profile.provider) if profile is not None else None
        )
        if flat is None:
            return (
                cls(adapter, profile.model)
                if adapter is not None and profile is not None
                else None
            )
        provider, voice = flat
        if adapter is not None:
            provider = adapter
        if profile is not None and profile.model:
            voice = profile.model
        return cls(provider, voice)

    def parameters(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        try:
            speed = float(settings.get("speed", 1.0))
        except (ValueError, TypeError):
            speed = 1.0
        values = dict(provider=self.provider, voice=self.voice, speed=speed)
        values["speech_voice"] = str(settings.get("speech_voice", "") or "")
        values.update(
            {
                name: bool(settings.get(name, False))
                for name in ("enabled", "auto_speak")
            }
        )
        return values


def _reference_audio(profile: Any) -> str:
    from gideon.integrations.voice.profiles import artifact_path

    relative = "locked.wav" if profile.locked else profile.ref_audio
    if relative:
        try:
            path = artifact_path(profile.id, relative)
            if path.is_file():
                return str(path)
        except Exception:
            pass
    return ""


def _profile_parameters(
    profile: Any, level: str, fallback_speed: float
) -> dict[str, Any]:
    names = ("ref_text", "seed", "instruct")
    extras = {name: getattr(profile, name) for name in names}
    return {
        "speed": profile.speed or fallback_speed,
        "profile_id": profile.id,
        "profile_level": level,
        "ref_audio": _reference_audio(profile),
        **extras,
        "design_params": dict(profile.design_params),
        "locked": profile.locked,
    }


def active_voice_params(*, surface: str = "", profile_id: str = "") -> dict | None:
    from gideon.extensions.providers.use_cases import load_use_case_settings
    from gideon.integrations.voice.bindings import resolve_profile_id
    from gideon.integrations.voice.profiles import get_profile

    resolved_id, level = resolve_profile_id(surface=surface, explicit=profile_id)
    profile = get_profile(resolved_id) if resolved_id else None
    selected = _VoiceSelection.resolve(active_tts(), profile)
    if selected is None:
        return None
    values = selected.parameters(load_use_case_settings("tts"))
    if profile is not None:
        values.update(_profile_parameters(profile, level, values["speed"]))
    return values


class CloningUnsupportedError(Exception):
    def __init__(self, provider: str):
        self.provider = provider
        self.reason = "cloning_unsupported"
        self.status = 409
        self.message = f"cloning_unsupported:{provider}"
        super().__init__(self.message)


def is_clone_request(params: Mapping[str, Any]) -> bool:
    return bool(params.get("ref_audio"))


def guard_synthesis_capability(
    provider: TtsProvider, params: Mapping[str, Any]
) -> None:
    if not is_clone_request(params):
        return
    if getattr(provider, "supports_cloning", False):
        return
    raise CloningUnsupportedError(getattr(provider, "name", "") or "")


def _synthesis_arguments(params: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        ("voice", str, ""),
        ("speed", float, 1.0),
        ("speech_voice", str, ""),
        ("ref_audio", str, ""),
        ("ref_text", str, ""),
        ("seed", int, 0),
        ("instruct", str, ""),
    )
    values: dict = {
        name: convert(params.get(name, default) or default)
        for name, convert, default in fields
    }
    values["design_params"] = dict(params.get("design_params") or {})
    return values


async def route_synthesis(
    params: Mapping[str, Any], text: str, *, output_path: str = ""
) -> str | None:
    adapter = params["provider"]
    guard_synthesis_capability(adapter, params)
    arguments = _synthesis_arguments(params)
    return await adapter.synthesize(text, output_path=output_path, **arguments)
