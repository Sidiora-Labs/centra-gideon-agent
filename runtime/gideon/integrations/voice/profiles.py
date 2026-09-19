"""Voice records and their contained audio artifacts."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KINDS = ("clone", "design")
HISTORY_MAX = 10
MIN_CONSENT_SECS = 1.0
MIN_CONSENT_BYTES = 16_000
_ARTIFACT_REF = "ref_audio"
_ARTIFACT_CONSENT = "consent"
_ARTIFACT_LOCKED = "locked"
READABLE_ARTIFACTS = (_ARTIFACT_REF, _ARTIFACT_LOCKED)
_STORE_LOCK = threading.RLock()


class VoiceProfileError(Exception):
    def __init__(self, message: str, status: int = 400, reason: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason or "invalid_request"


def _number(value: Any, cast: type, fallback: Any) -> Any:
    try:
        return cast(value)
    except (TypeError, ValueError):
        return fallback


@dataclass
class VoiceProfile:
    id: str
    name: str = ""
    kind: str = "design"
    provider: str = ""
    model: str = ""
    ref_audio: str = ""
    ref_text: str = ""
    design_params: dict[str, Any] = field(default_factory=dict)
    instruct: str = ""
    seed: int = 0
    language: str = ""
    speed: float = 1.0
    locked: bool = False
    locked_at: str = ""
    verified_own_voice: bool = False
    consent_text: str = ""
    consent_audio: str = ""
    consent_recorded_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        record = {item.name: getattr(self, item.name) for item in fields(self)}
        record.update(
            design_params=dict(self.design_params),
            history=list(map(dict, self.history)),
        )
        return record

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VoiceProfile:
        profile = cls(id="")
        for item in fields(profile):
            name = item.name
            default = getattr(profile, name)
            value = raw.get(name, default)
            decoded: Any
            if name == "design_params":
                decoded = dict(value) if isinstance(value, dict) else {}
            elif name == "history":
                decoded = (
                    [dict(entry) for entry in value if isinstance(entry, dict)]
                    if isinstance(value, list)
                    else []
                )
            elif isinstance(default, bool):
                decoded = bool(value)
            elif isinstance(default, (float, int)):
                decoded = _number(value, type(default), default)
            else:
                decoded = str(value or default)
            setattr(profile, name, decoded)
        return profile


def config_dir() -> Path:
    return config_loader.config_dir()


def profiles_root() -> Path:
    return config_dir().joinpath("voice_profiles")


def validate_id(profile_id: str) -> str:
    value = str(profile_id or "")
    if _ID_RE.match(value):
        return value
    raise VoiceProfileError(f"invalid profile id: {value!r}", 400, "invalid_profile_id")


def _within(root: Path, candidate: Path) -> Path:
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise VoiceProfileError(
            "path escapes the voice_profiles dir", 400, "path_escape"
        )
    return candidate


def _location(profile_id: str, suffix: str = "") -> Path:
    name = validate_id(profile_id) + suffix
    root = profiles_root()
    return _within(root, root / name)


def profile_path(profile_id: str) -> Path:
    return _location(profile_id, ".json")


def profile_dir(profile_id: str) -> Path:
    return _location(profile_id)


def artifact_path(profile_id: str, relative: str) -> Path:
    directory = profile_dir(profile_id)
    relative = str(relative or "").strip()
    parts = Path(relative).parts
    if not relative or relative.startswith("/") or "\x00" in relative or ".." in parts:
        raise VoiceProfileError(
            f"invalid artifact path: {relative!r}", 400, "invalid_artifact"
        )
    return _within(directory, directory / relative)


def _audio_at_least(path: Path, secs: float) -> bool:
    try:
        if not path.is_file():
            return False
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            if rate > 0:
                return audio.getnframes() >= secs * rate
    except Exception:
        logger.debug("Using encoded audio size for consent clip: %s", path.name)
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size >= MIN_CONSENT_BYTES


def consent_recording(profile_id: str) -> Path | None:
    try:
        directory = profile_dir(profile_id)
    except VoiceProfileError:
        return None
    if directory.is_dir():
        return next(
            (
                clip
                for clip in sorted(directory.glob("consent.*"))
                if not clip.is_symlink() and clip.is_file()
            ),
            None,
        )
    return None


def recompute_verified(profile: VoiceProfile) -> bool:
    if profile.consent_text.strip():
        recording = consent_recording(profile.id)
        return recording is not None and _audio_at_least(recording, MIN_CONSENT_SECS)
    return False


def assert_artifact_release_allowed(profile: VoiceProfile, artifact: str) -> None:
    failure = None
    if artifact not in READABLE_ARTIFACTS:
        failure = (f"artifact not readable: {artifact}", "artifact_not_readable")
    elif profile.kind == "clone" and not profile.verified_own_voice:
        failure = ("consent for this cloned voice is not verified", "consent_required")
    if failure:
        raise VoiceProfileError(failure[0], 403, failure[1])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_profile_id() -> str:
    return "vp-" + uuid.uuid4().hex[:8]


def _read(path: Path) -> VoiceProfile | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("Cannot decode voice record %s", path.name, exc_info=True)
        return None
    if not isinstance(record, dict):
        return None
    decoded = VoiceProfile.from_dict(record)
    if decoded.id:
        decoded.verified_own_voice = recompute_verified(decoded)
        return decoded
    return None


def _write(profile: VoiceProfile) -> None:
    with _STORE_LOCK:
        profile.updated_at, profile.verified_own_voice = _now(), recompute_verified(
            profile
        )
        profiles_root().mkdir(parents=True, exist_ok=True)
        payload = json.dumps(profile.to_dict(), indent=2)
        atomic_write(profile_path(profile.id), payload, mode=0o600)


def get_profile(profile_id: str) -> VoiceProfile | None:
    with _STORE_LOCK:
        record = profile_path(profile_id)
        return _read(record) if record.is_file() else None


def require_profile(profile_id: str) -> VoiceProfile:
    result = get_profile(profile_id)
    if result is not None:
        return result
    raise VoiceProfileError(f"no such voice profile: {profile_id}", 404, "not_found")


def list_profiles() -> list[VoiceProfile]:
    with _STORE_LOCK:
        root = profiles_root()
        records = []
        for path in sorted(root.glob("*.json")) if root.is_dir() else ():
            try:
                record = _read(_within(root, path))
            except VoiceProfileError:
                logger.warning("Skipping uncontained voice record %s", path.name)
                continue
            if record is not None:
                records.append(record)
        return sorted(records, key=lambda record: record.created_at, reverse=True)


_MUTABLE_FIELDS = (
    "name",
    "provider",
    "model",
    "ref_text",
    "design_params",
    "instruct",
    "seed",
    "language",
    "speed",
)


def _apply(profile: VoiceProfile, fields: dict[str, Any]) -> None:
    converters = {"seed": (int, "an integer"), "speed": (float, "a number")}
    for key in _MUTABLE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key in converters:
            convert, description = converters[key]
            try:
                value = convert(value)
            except (TypeError, ValueError) as error:
                raise VoiceProfileError(
                    f"{key} must be {description}", 400, f"invalid_{key}"
                ) from error
        elif key == "design_params":
            value = dict(value) if isinstance(value, dict) else {}
        else:
            value = str(value or "")
        setattr(profile, key, value)


@contextlib.contextmanager
def _edit(profile_id: str):
    with _STORE_LOCK:
        profile = require_profile(profile_id)
        yield profile
        _write(profile)


def create_profile(**fields: Any) -> VoiceProfile:
    kind, name = (
        str(fields.get("kind") or "design"),
        str(fields.get("name") or "").strip(),
    )
    if kind not in KINDS:
        raise VoiceProfileError(f"kind must be one of {KINDS}", 400, "invalid_kind")
    if not name:
        raise VoiceProfileError("name required", 400, "name_required")
    with _STORE_LOCK:
        result = VoiceProfile(new_profile_id(), name=name, kind=kind, created_at=_now())
        _apply(result, fields)
        profile_dir(result.id).mkdir(parents=True, exist_ok=True)
        _write(result)
    return result


def update_profile(profile_id: str, **fields: Any) -> VoiceProfile:
    with _edit(profile_id) as profile:
        if "kind" in fields and str(fields["kind"]) != profile.kind:
            raise VoiceProfileError("kind is immutable", 400, "kind_immutable")
        _apply(profile, fields)
    return profile


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def delete_profile(profile_id: str) -> bool:
    with _STORE_LOCK:
        name, root = validate_id(profile_id), profiles_root()
        record, directory = root / f"{name}.json", root / name
        found = not record.is_symlink() and record.is_file()
        _unlink(record)
        if directory.is_symlink():
            _unlink(directory)
        elif directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
        return found


def _audio_suffix(source: Path, suffix: str = "") -> str:
    extension = (suffix or Path(source).suffix or ".wav").lower()
    if re.match(r"^\.[A-Za-z0-9]{1,8}$", extension):
        return extension
    raise VoiceProfileError(
        f"invalid audio extension: {extension!r}", 400, "invalid_extension"
    )


def _drop_artifacts(profile_id: str, stem: str, keep: Path | None = None) -> None:
    for candidate in profile_dir(profile_id).glob(stem + ".*"):
        if candidate != keep:
            _unlink(candidate)


def _install_audio(profile_id: str, stem: str, source: Path, suffix: str) -> str:
    destination = artifact_path(profile_id, stem + _audio_suffix(source, suffix))
    destination.parent.mkdir(parents=True, exist_ok=True)
    _drop_artifacts(profile_id, stem, keep=destination)
    shutil.move(str(source), str(destination))
    destination.chmod(0o600)
    return destination.name


def attach_ref_audio(
    profile_id: str, source: Path, *, suffix: str = ""
) -> VoiceProfile:
    with _edit(profile_id) as profile:
        profile.ref_audio = _install_audio(profile_id, _ARTIFACT_REF, source, suffix)
    return profile


def attach_consent_audio(
    profile_id: str, source: Path, *, suffix: str = ""
) -> VoiceProfile:
    with _edit(profile_id) as profile:
        profile.consent_audio = _install_audio(
            profile_id, _ARTIFACT_CONSENT, source, suffix
        )
        profile.consent_recorded_at = profile.consent_recorded_at or _now()
    return profile


def record_consent(
    profile_id: str,
    *,
    consent_text: str,
    audio_source: Path | None = None,
    suffix: str = "",
) -> VoiceProfile:
    with _edit(profile_id) as profile:
        statement = str(consent_text or "").strip()
        if not statement:
            raise VoiceProfileError(
                "consent_text required", 400, "consent_text_required"
            )
        profile.consent_text = statement
        existing = consent_recording(profile_id)
        if existing is not None:
            profile.consent_audio = existing.name
        if audio_source is not None:
            profile.consent_audio = _install_audio(
                profile_id, _ARTIFACT_CONSENT, audio_source, suffix
            )
        profile.consent_recorded_at = _now()
    return profile


def revoke_consent(profile_id: str) -> VoiceProfile:
    with _edit(profile_id) as profile:
        _drop_artifacts(profile_id, _ARTIFACT_CONSENT)
        profile.consent_text = profile.consent_audio = profile.consent_recorded_at = ""
    return profile


def _copy_clip(profile_id: str, source: Path, relative: str) -> None:
    target = artifact_path(profile_id, relative)
    shutil.copyfile(source, target)
    target.chmod(0o600)


def _prune_history(profile: VoiceProfile) -> None:
    excess = max(0, len(profile.history) - HISTORY_MAX)
    removed, profile.history = profile.history[:excess], profile.history[excess:]
    for entry in removed:
        with contextlib.suppress(OSError, VoiceProfileError):
            _unlink(artifact_path(profile.id, str(entry.get("path") or "")))


def append_history(
    profile_id: str, audio: Path, *, seed: int = 0, text_hash: str = ""
) -> VoiceProfile:
    with _edit(profile_id) as profile:
        artifact_path(profile_id, "history").mkdir(parents=True, exist_ok=True)
        relative = "history/{}-{}.wav".format(
            int(time.time() * 1000), uuid.uuid4().hex[:6]
        )
        _copy_clip(profile_id, audio, relative)
        profile.history += [
            {
                "path": relative,
                "seed": int(seed),
                "text_hash": str(text_hash or ""),
                "created_at": _now(),
            }
        ]
        _prune_history(profile)
    return profile


def _history_entry(profile: VoiceProfile, history_index: int) -> dict[str, Any]:
    try:
        index = int(history_index)
    except (TypeError, ValueError) as error:
        raise VoiceProfileError(
            "history_index must be an integer", 400, "invalid_history_index"
        ) from error
    if not profile.history:
        raise VoiceProfileError(
            "profile has no generation history", 409, "empty_history"
        )
    if index not in range(len(profile.history)):
        raise VoiceProfileError(
            f"history_index out of range (0..{len(profile.history) - 1})",
            404,
            "history_index_out_of_range",
        )
    return profile.history[index]


def lock_profile(profile_id: str, history_index: int) -> VoiceProfile:
    with _edit(profile_id) as profile:
        entry = _history_entry(profile, history_index)
        source = artifact_path(profile_id, str(entry.get("path") or ""))
        if not source.is_file():
            raise VoiceProfileError(
                "that generation's audio is gone", 409, "history_audio_missing"
            )
        _copy_clip(profile_id, source, "locked.wav")
        profile.locked, profile.locked_at = True, _now()
        profile.seed = _number(entry.get("seed") or 0, int, 0)
    return profile


def unlock_profile(profile_id: str) -> VoiceProfile:
    with _edit(profile_id) as profile:
        with contextlib.suppress(OSError, VoiceProfileError):
            _unlink(artifact_path(profile_id, "locked.wav"))
        profile.locked, profile.locked_at, profile.seed = False, "", 0
    return profile


def profile_payload(profile: VoiceProfile) -> dict[str, Any]:
    clips = {
        _ARTIFACT_REF: profile.ref_audio,
        _ARTIFACT_CONSENT: profile.consent_audio,
        _ARTIFACT_LOCKED: "locked.wav",
    }
    availability = dict.fromkeys(clips, False)
    for name, relative in clips.items():
        if relative:
            with contextlib.suppress(VoiceProfileError):
                availability[name] = artifact_path(profile.id, relative).is_file()
    return profile.to_dict() | {
        "artifacts": availability,
        "history_count": len(profile.history),
    }
