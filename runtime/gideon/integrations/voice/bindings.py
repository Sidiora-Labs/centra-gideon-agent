"""Persist surface assignments and resolve the voice selection ladder."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.integrations.voice.profiles import (
    VoiceProfile,
    VoiceProfileError,
    get_profile,
    validate_id,
)

logger = logging.getLogger(__name__)
DEFAULT_KEY = "default"
SURFACE_NAMESPACES = ("channel", "agent", "client")
_SURFACE_RE = re.compile(r"^(channel|agent|client):[A-Za-z0-9_.\-]{1,64}$")
LEVEL_EXPLICIT = "explicit"
LEVEL_BINDING = "binding"
LEVEL_DEFAULT = "default"
LEVEL_BUILTIN = "built-in"


def config_dir() -> Path:
    return config_loader.config_dir()


def bindings_path() -> Path:
    return config_dir().joinpath("voice_bindings.json")


def validate_surface(surface: str) -> str:
    candidate = str(surface or "").strip()
    if candidate == DEFAULT_KEY or _SURFACE_RE.match(candidate):
        return candidate
    raise VoiceProfileError(
        f"surface must be 'default' or <{'|'.join(SURFACE_NAMESPACES)}>:<name>",
        400,
        "invalid_surface",
    )


class _BindingLedger:
    def __init__(self) -> None:
        self.lock = threading.RLock()

    def read(self) -> dict[str, str]:
        with self.lock:
            path = bindings_path()
            if not path.is_file():
                return {}
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Cannot read voice bindings; using an empty map")
                return {}
            if not isinstance(record, dict):
                return {}
            return {
                str(key): value
                for key, value in record.items()
                if self.accepts(key, value)
            }

    @staticmethod
    def accepts(key: object, value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            validate_surface(str(key))
            validate_id(value)
            return True
        except VoiceProfileError:
            logger.warning("Ignoring malformed voice binding %r", key)
            return False

    def write(self, assignments: dict[str, str]) -> None:
        with self.lock:
            atomic_write(bindings_path(), json.dumps(assignments, indent=2), mode=0o600)

    def assign(self, key: str, profile_id: str | None) -> dict[str, str]:
        with self.lock:
            assignments = self.read()
            if profile_id is None:
                assignments.pop(key, None)
            else:
                assignments[key] = profile_id
            self.write(assignments)
            return assignments

    def forget(self, profile_id: str) -> dict[str, str]:
        with self.lock:
            assignments = self.read()
            retained = dict(
                filter(lambda entry: entry[1] != profile_id, assignments.items())
            )
            if len(retained) != len(assignments):
                self.write(retained)
            return retained


_ledger = _BindingLedger()


@dataclass(frozen=True)
class _Candidate:
    profile_id: str
    level: str

    def resolve(self) -> tuple[str, str] | None:
        if self.profile_id and get_profile(self.profile_id) is not None:
            return self.profile_id, self.level
        if self.level == LEVEL_EXPLICIT:
            raise VoiceProfileError(
                f"no such voice profile: {self.profile_id}", 404, "not_found"
            )
        return None


def load_bindings() -> dict[str, str]:
    return _ledger.read()


def save_bindings(bindings: dict[str, str]) -> None:
    _ledger.write(bindings)


def set_binding(surface: str, profile_id: str) -> dict[str, str]:
    surface, profile_id = validate_surface(surface), validate_id(profile_id)
    _Candidate(profile_id, LEVEL_EXPLICIT).resolve()
    return _ledger.assign(surface, profile_id)


def clear_binding(surface: str) -> dict[str, str]:
    return _ledger.assign(validate_surface(surface), None)


def forget_profile(profile_id: str) -> dict[str, str]:
    return _ledger.forget(profile_id)


def resolve_profile_id(*, surface: str = "", explicit: str = "") -> tuple[str, str]:
    candidates: tuple[_Candidate, ...]
    if explicit:
        candidates = (_Candidate(validate_id(explicit), LEVEL_EXPLICIT),)
    else:
        assignments = load_bindings()
        surface = str(surface or "").strip()
        candidates = (
            _Candidate(
                assignments.get(surface, "") if surface != DEFAULT_KEY else "",
                LEVEL_BINDING,
            ),
            _Candidate(assignments.get(DEFAULT_KEY, ""), LEVEL_DEFAULT),
        )
    for candidate in candidates:
        selection = candidate.resolve()
        if selection is not None:
            return selection
    return "", LEVEL_BUILTIN


def binding_warning(profile: VoiceProfile, surface: str) -> str:
    exposed = str(surface or "").partition(":")[0] in SURFACE_NAMESPACES
    unverified = profile.kind == "clone" and not profile.verified_own_voice
    return "unverified_clone_consent" if exposed and unverified else ""
