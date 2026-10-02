"""Two-phase provisioning for reviewed local Hypermid enrollment."""

from __future__ import annotations

import json
import os
import secrets
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from gideon.core.config.loader import config_dir

from .foundation import Digest, Id
from .lifecycle import LocalEnrollment
from .models import JsonValue, Scope
from .operations import ActionReceipt

_GRANT_FIELDS = frozenset({"operations", "resources", "expires_ms"})
_MAX_RESOURCES = 256


class EnrollmentProvisioningError(ValueError):
    """Raised when reviewed enrollment cannot be staged or published safely."""


@dataclass(frozen=True, slots=True)
class EnrollmentReceipt:
    capability_id: Id
    scope: Scope
    operations: tuple[str, ...]
    resources: tuple[Id, ...]
    expires_ms: int
    config_digest: Digest
    target_version: str


@dataclass(slots=True)
class StagedEnrollment:
    scope: Scope
    reviewed_plan_digest: Digest
    target_version: str
    _enrollment: LocalEnrollment = field(repr=False)
    _content: bytes = field(repr=False)
    _destination: Path = field(repr=False)
    _staged_path: Path | None = field(repr=False)
    _complete: bool = field(default=False, init=False, repr=False)

    @property
    def local_enrollment(self) -> LocalEnrollment:
        """Return the staged authority for one pre-publication daemon launch."""

        if self._complete:
            raise EnrollmentProvisioningError("enrollment stage is already complete")
        return self._enrollment

    def commit(self, *, lifecycle_receipt: ActionReceipt) -> EnrollmentReceipt:
        if self._complete:
            raise EnrollmentProvisioningError("enrollment stage is already complete")
        if (
            lifecycle_receipt.operation != "lifecycle.install.apply"
            or lifecycle_receipt.state != "committed"
            or lifecycle_receipt.scope != self.scope
            or lifecycle_receipt.plan_digest != self.reviewed_plan_digest
        ):
            raise EnrollmentProvisioningError(
                "committed install receipt does not bind the reviewed enrollment plan"
            )

        if self._staged_path is None:
            if self._destination.read_bytes() != self._content:
                raise EnrollmentProvisioningError(
                    "local enrollment changed before publication"
                )
        else:
            created = False
            try:
                os.link(self._staged_path, self._destination)
                created = True
            except FileExistsError as error:
                if self._destination.read_bytes() != self._content:
                    raise EnrollmentProvisioningError(
                        "local enrollment changed before publication"
                    ) from error
            finally:
                self._staged_path.unlink(missing_ok=True)
            try:
                _sync_directory(self._destination.parent)
            except BaseException:
                if created and self._destination.read_bytes() == self._content:
                    self._destination.unlink(missing_ok=True)
                    try:
                        _sync_directory(self._destination.parent)
                    except OSError:
                        pass
                raise

        self._complete = True
        return EnrollmentReceipt(
            capability_id=self._enrollment.capability_id,
            scope=self.scope,
            operations=self._enrollment.operations,
            resources=self._enrollment.resources,
            expires_ms=self._enrollment.expires_ms,
            config_digest=Digest.sha256(self._content),
            target_version=self.target_version,
        )

    def abort(self) -> None:
        if self._staged_path is not None:
            self._staged_path.unlink(missing_ok=True)
        self._complete = True


def stage_enrollment(
    *,
    scope: Scope,
    reviewed_plan_digest: str,
    target_version: str,
    params: Mapping[str, JsonValue],
) -> StagedEnrollment:
    """Stage the exact local grant returned in a reviewed install plan."""

    try:
        digest = Digest(reviewed_plan_digest)
    except ValueError as error:
        raise EnrollmentProvisioningError("reviewed plan digest is invalid") from error
    if (
        not isinstance(target_version, str)
        or not target_version.strip()
        or len(target_version) > 80
    ):
        raise EnrollmentProvisioningError("target version is invalid")
    operations, resources, expires_ms = _reviewed_grant(params)
    destination = config_dir() / "hypermid" / "enrollment.json"
    _prepare_directory(destination.parent)

    existing = _load_existing(destination, scope)
    if existing is not None:
        if (
            existing.operations != operations
            or existing.resources != resources
            or existing.expires_ms != expires_ms
        ):
            raise EnrollmentProvisioningError(
                "existing local enrollment does not match the reviewed grant"
            )
        content = _encode(existing)
        if destination.read_bytes() != content:
            raise EnrollmentProvisioningError(
                "existing local enrollment is not canonically encoded"
            )
        return StagedEnrollment(
            scope,
            digest,
            target_version.strip(),
            existing,
            content,
            destination,
            None,
        )

    enrollment = LocalEnrollment(
        scope=scope,
        credential_id=Id(f"local-credential-{secrets.token_hex(16)}"),
        capability_id=Id(f"local-capability-{secrets.token_hex(16)}"),
        operations=operations,
        resources=resources,
        expires_ms=expires_ms,
    )
    content = _encode(enrollment)
    staged_path = _write_stage(destination.parent, content)
    return StagedEnrollment(
        scope,
        digest,
        target_version.strip(),
        enrollment,
        content,
        destination,
        staged_path,
    )


def validate_enrollment_params(params: Mapping[str, JsonValue]) -> None:
    """Validate the exact non-secret grant fields before plan review."""

    _reviewed_grant(params)


def _reviewed_grant(
    params: Mapping[str, JsonValue],
) -> tuple[tuple[str, ...], tuple[Id, ...], int]:
    if not isinstance(params, Mapping) or set(params) != _GRANT_FIELDS:
        raise EnrollmentProvisioningError(
            "reviewed local enrollment fields are missing or changed"
        )
    raw_operations = params.get("operations")
    raw_resources = params.get("resources")
    expires_ms = params.get("expires_ms")
    if (
        not isinstance(raw_operations, list)
        or not raw_operations
        or not all(isinstance(item, str) for item in raw_operations)
        or len(set(raw_operations)) != len(raw_operations)
    ):
        raise EnrollmentProvisioningError(
            "reviewed local enrollment operations are invalid"
        )
    if (
        not isinstance(raw_resources, list)
        or not raw_resources
        or len(raw_resources) > _MAX_RESOURCES
        or not all(isinstance(item, str) for item in raw_resources)
        or len(set(raw_resources)) != len(raw_resources)
        or any(
            item.casefold() in {"all", "any", "*"} or "*" in item
            for item in raw_resources
        )
    ):
        raise EnrollmentProvisioningError(
            "reviewed local enrollment resources must be exact identifiers"
        )
    if (
        isinstance(expires_ms, bool)
        or not isinstance(expires_ms, int)
        or expires_ms <= time.time_ns() // 1_000_000
    ):
        raise EnrollmentProvisioningError(
            "reviewed local enrollment expiry must be in the future"
        )
    try:
        enrollment = LocalEnrollment(
            scope=Scope("validation-owner", "validation-project"),
            credential_id=Id("validation-credential"),
            capability_id=Id("validation-capability"),
            operations=tuple(raw_operations),
            resources=tuple(Id(item) for item in raw_resources),
            expires_ms=expires_ms,
        )
    except ValueError as error:
        raise EnrollmentProvisioningError(
            "reviewed local enrollment grant is invalid"
        ) from error
    return enrollment.operations, enrollment.resources, enrollment.expires_ms


def _load_existing(path: Path, scope: Scope) -> LocalEnrollment | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise EnrollmentProvisioningError("local enrollment path is unreadable") from error
    try:
        return LocalEnrollment.load(path, scope=scope)
    except (OSError, ValueError, PermissionError) as error:
        raise EnrollmentProvisioningError(
            "existing local enrollment is invalid or unavailable"
        ) from error


def _prepare_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EnrollmentProvisioningError(
            "local enrollment directory must be a real directory"
        )
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise EnrollmentProvisioningError(
            "local enrollment directory has the wrong owner"
        )
    os.chmod(path, 0o700)


def _encode(enrollment: LocalEnrollment) -> bytes:
    value = {
        "scope": enrollment.scope.to_wire(),
        "credential_id": str(enrollment.credential_id),
        "capability_id": str(enrollment.capability_id),
        "operations": list(enrollment.operations),
        "resources": [str(item) for item in enrollment.resources],
        "expires_ms": enrollment.expires_ms,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_stage(directory: Path, content: bytes) -> Path:
    while True:
        path = directory / f".enrollment-{secrets.token_hex(16)}.stage"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            continue
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "EnrollmentProvisioningError",
    "EnrollmentReceipt",
    "StagedEnrollment",
    "stage_enrollment",
    "validate_enrollment_params",
]
