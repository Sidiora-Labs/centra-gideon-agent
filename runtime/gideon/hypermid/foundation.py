from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TypeAlias, cast

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)

MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_RECOVERY_COMPONENTS = 64
MAX_SYMLINK_COMPONENTS = 40
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class ContractViolation(ValueError):
    pass


class Id(str):
    def __new__(cls, value: object) -> Id:
        if (
            not isinstance(value, str)
            or len(value) > 160
            or not _ID_PATTERN.fullmatch(value)
        ):
            raise ContractViolation("Id does not match the Hypermid Id contract")
        return str.__new__(cls, value)


class Digest(str):
    def __new__(cls, value: object) -> Digest:
        if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
            raise ContractViolation(
                "digest must be exactly 64 lowercase hexadecimal characters"
            )
        return str.__new__(cls, value)

    @classmethod
    def sha256(cls, value: bytes) -> Digest:
        return cls(hashlib.sha256(value).hexdigest())


@dataclass(frozen=True, slots=True)
class Scope:
    owner_id: Id
    project_id: Id
    workspace_id: Id | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", Id(self.owner_id))
        object.__setattr__(self, "project_id", Id(self.project_id))
        if self.workspace_id is not None:
            object.__setattr__(self, "workspace_id", Id(self.workspace_id))

    def to_wire(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "owner_id": str(self.owner_id),
            "project_id": str(self.project_id),
        }
        if self.workspace_id is not None:
            value["workspace_id"] = str(self.workspace_id)
        return value

    @classmethod
    def from_wire(cls, value: object) -> Scope:
        _require_keys(value, {"owner_id", "project_id"}, {"workspace_id"})
        value = cast(Mapping[str, Any], value)
        return cls(
            owner_id=Id(value["owner_id"]),
            project_id=Id(value["project_id"]),
            workspace_id=Id(value["workspace_id"]) if "workspace_id" in value else None,
        )


@dataclass(frozen=True, order=True, slots=True)
class Cursor:
    epoch: int
    sequence: int

    def __post_init__(self) -> None:
        if isinstance(self.epoch, bool) or not 1 <= self.epoch <= MAX_SAFE_INTEGER:
            raise ContractViolation("cursor epoch is outside the Hypermid wire range")
        if (
            isinstance(self.sequence, bool)
            or not 0 <= self.sequence <= MAX_SAFE_INTEGER
        ):
            raise ContractViolation(
                "cursor sequence is outside the Hypermid wire range"
            )

    def next(self) -> Cursor:
        return Cursor(self.epoch, self.sequence + 1)

    def to_wire(self) -> dict[str, JsonValue]:
        return {"epoch": self.epoch, "sequence": self.sequence}

    @classmethod
    def from_wire(cls, value: object) -> Cursor:
        _require_keys(value, {"epoch", "sequence"})
        value = cast(Mapping[str, Any], value)
        return cls(value["epoch"], value["sequence"])


class EffectState(str, Enum):
    NOT_STARTED = "not_started"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Error:
    code: str
    message: str
    retryable: bool
    retry_after_ms: int | None = None
    effect_state: EffectState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _ERROR_CODE_PATTERN.fullmatch(
            self.code
        ):
            raise ContractViolation(
                "error code does not match the Hypermid Error contract"
            )
        if not isinstance(self.message, str) or len(self.message) > 2_048:
            raise ContractViolation("error message exceeds the Hypermid Error contract")
        if not isinstance(self.retryable, bool):
            raise ContractViolation("retryable must be a boolean")
        if self.retry_after_ms is not None and (
            isinstance(self.retry_after_ms, bool)
            or not 0 <= self.retry_after_ms <= 86_400_000
        ):
            raise ContractViolation("retry delay exceeds the Hypermid Error contract")
        if self.effect_state is not None and not isinstance(
            self.effect_state, EffectState
        ):
            object.__setattr__(self, "effect_state", EffectState(self.effect_state))

    def to_wire(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.retry_after_ms is not None:
            value["retry_after_ms"] = self.retry_after_ms
        if self.effect_state is not None:
            value["effect_state"] = self.effect_state.value
        return value

    @classmethod
    def from_wire(cls, value: object) -> Error:
        _require_keys(
            value,
            {"code", "message", "retryable"},
            {"retry_after_ms", "effect_state"},
        )
        value = cast(Mapping[str, Any], value)
        return cls(
            code=value["code"],
            message=value["message"],
            retryable=value["retryable"],
            retry_after_ms=value.get("retry_after_ms"),
            effect_state=(
                EffectState(value["effect_state"]) if "effect_state" in value else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Trace:
    trace_id: Id
    request_id: Id

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", Id(self.trace_id))
        object.__setattr__(self, "request_id", Id(self.request_id))

    def to_wire(self) -> dict[str, JsonValue]:
        return {"trace_id": str(self.trace_id), "request_id": str(self.request_id)}

    @classmethod
    def from_wire(cls, value: object) -> Trace:
        _require_keys(value, {"trace_id", "request_id"})
        value = cast(Mapping[str, Any], value)
        return cls(Id(value["trace_id"]), Id(value["request_id"]))


class Admission(str, Enum):
    LIVE = "live"
    RECOVERY_ONLY = "recovery_only"


@dataclass(frozen=True, slots=True)
class ProjectRoot:
    scope: Scope
    canonical_path: Path
    admission: Admission
    missing_components: int = 0

    @property
    def permits_new_state(self) -> bool:
        return self.admission is Admission.LIVE

    def require_live(self) -> None:
        if not self.permits_new_state:
            raise ContractViolation(
                "recovery-only identity cannot create durable state"
            )

    def to_wire(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_wire(),
            "canonical_path": os.fspath(self.canonical_path),
            "admission": self.admission.value,
        }


def resolve_project_root(
    scope: Scope,
    path: str | os.PathLike[str],
    admission: Admission = Admission.LIVE,
) -> ProjectRoot:
    candidate = Path(path).expanduser()
    if not os.fspath(candidate):
        raise ContractViolation("project path is empty")
    candidate = candidate.absolute()
    if admission is Admission.LIVE:
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        _check_symlink_bound(candidate)
        return ProjectRoot(scope, candidate.resolve(strict=True), admission)

    existing = candidate
    tail: list[str] = []
    while not existing.exists():
        if len(tail) >= MAX_RECOVERY_COMPONENTS or existing.parent == existing:
            raise ContractViolation("recovery path has no bounded existing prefix")
        tail.append(existing.name)
        existing = existing.parent
    _check_symlink_bound(existing)
    recovered = existing.resolve(strict=True)
    for component in reversed(tail):
        recovered /= component
    return ProjectRoot(scope, recovered, admission, len(tail))


def normalize_windows_spelling(value: str) -> str:
    value = value.replace("/", "\\")
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[len("\\\\?\\UNC\\") :]
    elif value.startswith("\\\\?\\"):
        value = value[len("\\\\?\\") :]
    if len(value) >= 2 and value[1] == ":":
        value = value[0].upper() + value[1:]
    while len(value) > 3 and value.endswith("\\"):
        value = value[:-1]
    return value


@dataclass(frozen=True, slots=True)
class HomeResolution:
    path: Path
    is_relative: bool


def resolve_home(
    value: str | os.PathLike[str] | None, fallback: str | os.PathLike[str]
) -> HomeResolution:
    selected = Path(value) if value is not None and os.fspath(value) else Path(fallback)
    return HomeResolution(selected, not selected.is_absolute())


def module_path(home: HomeResolution, module_id: Id) -> Path:
    return home.path / str(Id(module_id))


def _check_symlink_bound(path: Path) -> None:
    current = Path(path.anchor)
    count = 0
    for component in path.parts[1:] if path.anchor else path.parts:
        current /= component
        try:
            if current.is_symlink():
                count += 1
        except OSError as exc:
            raise ContractViolation(f"cannot inspect project path: {exc}") from exc
        if count > MAX_SYMLINK_COMPONENTS:
            raise ContractViolation("path exceeds the bounded symlink-component limit")


def _require_keys(
    value: object, required: set[str], optional: set[str] | None = None
) -> None:
    if not isinstance(value, Mapping):
        raise ContractViolation("wire value must be an object")
    optional = optional or set()
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise ContractViolation("wire value has missing or unknown fields")


__all__ = [
    "Admission",
    "ContractViolation",
    "Cursor",
    "Digest",
    "EffectState",
    "Error",
    "HomeResolution",
    "Id",
    "ProjectRoot",
    "Scope",
    "Trace",
    "module_path",
    "normalize_windows_spelling",
    "resolve_home",
    "resolve_project_root",
]
