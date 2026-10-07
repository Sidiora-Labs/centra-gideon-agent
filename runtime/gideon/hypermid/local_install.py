"""Pre-daemon reviewed install authority for a local Hypermid runtime."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from .client import HypermidClient
from .enrollment_provisioning import (
    current_enrollment_review,
    stage_enrollment,
    validate_enrollment_params,
)
from .enrollment_source import NativeEnrollmentSource
from .foundation import Digest
from .lifecycle import HypermidLifecycle
from .models import JsonValue, Scope
from .operations import ActionReceipt, PlanStep
from .operator_lifecycle import LifecyclePlan, LifecycleReceipt

_PLAN_LIFETIME = timedelta(minutes=10)


class LocalInstallError(RuntimeError):
    """Raised when pre-daemon install authority cannot safely complete."""


class LocalInstallAuthority:
    def __init__(
        self,
        lifecycle: HypermidLifecycle,
        *,
        executable: str | Path,
        scope: Scope,
        enrollment_source: NativeEnrollmentSource,
    ) -> None:
        self.lifecycle = lifecycle
        self.executable = Path(executable)
        self.scope = scope
        self.enrollment_source = enrollment_source

    async def plan(
        self,
        action: str,
        *,
        target_version: str | None = None,
        data_disposition: str | None = None,
        source: str | None = None,
        destination: str | None = None,
        resume_after: object | None = None,
        params: Mapping[str, JsonValue] | None = None,
    ) -> LifecyclePlan:
        if action != "install":
            raise LocalInstallError(
                "pre-daemon lifecycle authority only supports install"
            )
        if any(
            value is not None
            for value in (data_disposition, source, destination, resume_after)
        ):
            raise LocalInstallError(
                "local install contains unsupported lifecycle inputs"
            )
        if not isinstance(target_version, str) or not target_version.strip():
            raise LocalInstallError("local install target version is required")
        if len(target_version) > 80:
            raise LocalInstallError("local install target version is invalid")
        if params:
            raise LocalInstallError(
                "local install enrollment is authored by the local server"
            )
        local_enrollment = self.enrollment_source.issue(scope=self.scope)
        validate_enrollment_params(local_enrollment)
        current = current_enrollment_review(self.scope)
        refresh = current is not None
        if (
            refresh
            and cast(HypermidClient, self.lifecycle.adapter.client).connected
            and (
                self.lifecycle.process is None
                or self.lifecycle.process.returncode is not None
            )
        ):
            raise LocalInstallError(
                "reviewed enrollment refresh requires the owned daemon"
            )
        additions: dict[str, JsonValue] = {
            "operations": [
                value
                for value in cast(list[str], local_enrollment["operations"])
                if current is None
                or value not in cast(list[str], current["operations"])
            ],
            "resources": [
                value
                for value in cast(list[str], local_enrollment["resources"])
                if current is None or value not in cast(list[str], current["resources"])
            ],
        }

        executable = self._verified_executable()
        binary_digest = _file_digest(executable)
        identity = _operator_identity()
        created = datetime.now(timezone.utc)
        body: dict[str, JsonValue] = {
            "operation": "lifecycle.install.apply",
            "scope": self.scope.to_wire(),
            "created_at": _timestamp(created),
            "expires_at": _timestamp(created + _PLAN_LIFETIME),
            "destructive": False,
            "restart_required": True,
            "steps": [
                {
                    "id": "verify-local-authority",
                    "title": "Verify local operator and packaged daemon",
                    "effect": "read",
                    "state": "planned",
                },
                {
                    "id": "enroll-local-runtime",
                    "title": "Enroll and start the local Hypermid runtime",
                    "effect": "write_authoritative",
                    "state": "planned",
                },
            ],
            "blockers": [],
            "authority_digest": _object_digest(
                {"operator_identity": identity, "scope": self.scope.to_wire()}
            ),
            "blocker_digest": _object_digest([]),
            "target_version": target_version.strip(),
            "binary_path": str(executable),
            "binary_digest": binary_digest,
            "operator_identity": identity,
            "params": {
                "local_enrollment": dict(local_enrollment),
                "current_enrollment": current,
                "permission_additions": additions,
            },
            "checks": {
                "platform_supported": True,
                "directories_writable": True,
                "connection_material_protected": True,
                "ownership_available": True,
                "packaged_binary_verified": True,
            },
            "inventory": {},
            "exclusions": [],
        }
        plan_digest = _object_digest(body)
        raw = {
            "plan_id": f"local-install-{plan_digest[:24]}",
            "plan_digest": plan_digest,
            **body,
        }
        return LifecyclePlan.from_wire("install", raw, self.scope)

    async def apply(
        self,
        plan: LifecyclePlan,
        *,
        reviewed_digest: str,
        confirm_destructive: bool = False,
        confirm_purge: bool = False,
    ) -> LifecycleReceipt:
        if confirm_destructive or confirm_purge:
            raise LocalInstallError(
                "local install does not accept destructive confirmation"
            )
        if plan.action != "install" or plan.scope != self.scope:
            raise LocalInstallError("local install plan scope or operation changed")
        if reviewed_digest != plan.plan_digest or plan.plan.blockers:
            raise LocalInstallError("local install plan was not reviewed or is blocked")
        raw = plan.plan.raw
        if not isinstance(raw, Mapping):
            raise LocalInstallError("local install plan authority is unavailable")
        signed_body = {
            key: value
            for key, value in raw.items()
            if key not in {"plan_id", "plan_digest"}
        }
        if _object_digest(signed_body) != plan.plan_digest:
            raise LocalInstallError("local install plan changed after review")
        self._revalidate_plan(raw)
        raw_params = cast(Mapping[str, JsonValue], raw.get("params"))
        local_enrollment = (
            raw_params.get("local_enrollment")
            if isinstance(raw_params, Mapping)
            else None
        )
        if not isinstance(local_enrollment, Mapping) or plan.target_version is None:
            raise LocalInstallError(
                "reviewed local enrollment authority is unavailable"
            )

        staged = stage_enrollment(
            scope=self.scope,
            reviewed_plan_digest=plan.plan_digest,
            target_version=plan.target_version,
            params=local_enrollment,
            expected_current_digest=cast(
                str | None,
                (
                    cast(
                        Mapping[str, JsonValue],
                        raw_params.get("current_enrollment", {}),
                    ).get("digest")
                    if isinstance(raw_params.get("current_enrollment"), Mapping)
                    else None
                ),
            ),
        )
        refresh = isinstance(raw_params.get("current_enrollment"), Mapping)
        previous_enrollment = self.lifecycle.enrollment
        started_at = datetime.now(timezone.utc)
        started = False
        try:
            if not refresh:
                self.lifecycle.enrollment = staged.local_enrollment
                status = await self.lifecycle.start()
                if not status.available or not status.healthy:
                    raise LocalInstallError(
                        "local Hypermid daemon did not become healthy"
                    )
                started = True
                await self.lifecycle._validate_enrollment_grants()
            finished_at = datetime.now(timezone.utc)
            receipt = ActionReceipt(
                job_id=f"local-install-{plan.plan_digest[:24]}",
                operation="lifecycle.install.apply",
                scope=self.scope,
                plan_digest=plan.plan_digest,
                state="committed",
                started_at=_timestamp(started_at),
                finished_at=_timestamp(finished_at),
                cursor=None,
                steps=(
                    PlanStep(
                        "verify-local-authority",
                        "Verify local operator and packaged daemon",
                        "read",
                        "committed",
                    ),
                    PlanStep(
                        "enroll-local-runtime",
                        "Enroll and start the local Hypermid runtime",
                        "write_authoritative",
                        "committed",
                    ),
                ),
                rollback_available=False,
                artifact_digest=str(raw["binary_digest"]),
                error=None,
            )
            if refresh:

                def publish():
                    nonlocal receipt
                    receipt = replace(
                        receipt, finished_at=_timestamp(datetime.now(timezone.utc))
                    )
                    return staged.commit(lifecycle_receipt=receipt)

                enrollment = await self.lifecycle.reviewed_enrollment_restart(
                    staged.local_enrollment,
                    verify_current=staged.verify_current,
                    finalize=publish,
                )
            else:
                enrollment = staged.commit(lifecycle_receipt=receipt)
            return LifecycleReceipt(receipt, None, None, enrollment)
        except BaseException:
            staged.abort()
            if not refresh:
                if started or self.lifecycle.process is not None:
                    await self.lifecycle.stop()
                self.lifecycle.enrollment = previous_enrollment
            raise

    def _verified_executable(self) -> Path:
        try:
            info = self.executable.lstat()
            executable = self.executable.resolve(strict=True)
        except OSError as error:
            raise LocalInstallError(
                "packaged Hypermid daemon is unavailable"
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise LocalInstallError("packaged Hypermid daemon must be a regular file")
        if not os.access(executable, os.X_OK):
            raise LocalInstallError("packaged Hypermid daemon is not executable")
        return executable

    def _revalidate_plan(self, raw: Mapping[str, JsonValue]) -> None:
        expected_identity = _operator_identity()
        if raw.get("operator_identity") != expected_identity:
            raise LocalInstallError("local operator identity changed after review")
        if raw.get("scope") != self.scope.to_wire():
            raise LocalInstallError("local install scope changed after review")
        expires_at = raw.get("expires_at")
        if not isinstance(expires_at, str) or _parse_timestamp(
            expires_at
        ) <= datetime.now(timezone.utc):
            raise LocalInstallError("local install plan expired")
        params = raw.get("params")
        expected = (
            params.get("current_enrollment") if isinstance(params, Mapping) else None
        )
        if current_enrollment_review(self.scope) != expected:
            raise LocalInstallError("local enrollment changed after review")
        executable = self._verified_executable()
        if raw.get("binary_path") != str(executable):
            raise LocalInstallError(
                "packaged Hypermid daemon path changed after review"
            )
        if raw.get("binary_digest") != _file_digest(executable):
            raise LocalInstallError("packaged Hypermid daemon changed after review")


def _operator_identity() -> dict[str, JsonValue]:
    identity: dict[str, JsonValue] = {"username": getpass.getuser()}
    if hasattr(os, "geteuid"):
        identity["uid"] = os.geteuid()
    return identity


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _object_digest(value: object) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return str(Digest.sha256(content))


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LocalInstallError("local install plan timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise LocalInstallError("local install plan timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


__all__ = ["LocalInstallAuthority", "LocalInstallError"]
