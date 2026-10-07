"""Safe Hypermid lifecycle and portability client operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from .client import HypermidClient
from .enrollment_provisioning import (
    EnrollmentReceipt,
    StagedEnrollment,
    stage_enrollment,
)
from .models import Cursor, JsonValue, Scope
from .operations import (
    ActionPlan,
    ActionReceipt,
    OperatorContractError,
    _digest,
    _object,
    _text,
)

LifecycleOperation = Literal[
    "install", "update", "uninstall", "migrate", "export", "restore", "rollback"
]
DataDisposition = Literal["retain", "export", "purge"]
RecoveryState = Literal[
    "committed",
    "resumable",
    "rollback_available",
    "failed",
    "cancelled",
    "outcome_unknown",
]

_OPERATIONS = frozenset(
    {"install", "update", "uninstall", "migrate", "export", "restore", "rollback"}
)
_EXPORT_EXCLUSIONS = frozenset(
    {
        "credentials",
        "local_authentication_material",
        "vectors",
        "indexes",
        "leases",
        "jobs",
        "budget_reservations",
    }
)


@dataclass(frozen=True, slots=True)
class LifecyclePlan:
    action: LifecycleOperation
    plan: ActionPlan
    current_version: str | None
    target_version: str | None
    data_disposition: DataDisposition | None
    source_digest: str | None
    destination: str | None
    rollback_digest: str | None
    staging_id: str | None
    resume_after: Cursor | None
    estimated_items: int | None
    estimated_bytes: int | None
    inventory: Mapping[str, tuple[str, ...]]
    exclusions: frozenset[str]

    @property
    def plan_digest(self) -> str:
        return self.plan.plan_digest

    @property
    def scope(self) -> Scope:
        return self.plan.scope

    @classmethod
    def from_wire(
        cls, action: LifecycleOperation, value: object, expected_scope: Scope
    ) -> LifecyclePlan:
        raw = _object(value, "lifecycle plan")
        base = ActionPlan.from_wire(raw, expected_scope)
        expected_operation = f"lifecycle.{action}.apply"
        if base.operation != expected_operation:
            raise OperatorContractError(
                f"lifecycle plan operation must be {expected_operation}"
            )
        current_version = raw.get("current_version")
        target_version = raw.get("target_version")
        disposition = raw.get("data_disposition")
        source_digest = raw.get("source_digest")
        destination = raw.get("destination")
        rollback_digest = raw.get("rollback_digest")
        staging_id = raw.get("staging_id")
        resume_after = raw.get("resume_after")
        estimated_items = raw.get("estimated_items")
        estimated_bytes = raw.get("estimated_bytes")
        inventory = raw.get("inventory", {})
        exclusions = raw.get("exclusions", [])
        if current_version is not None:
            current_version = _text(current_version, "current_version", 80)
        if target_version is not None:
            target_version = _text(target_version, "target_version", 80)
        if disposition is not None and disposition not in ("retain", "export", "purge"):
            raise OperatorContractError("data_disposition is invalid")
        if source_digest is not None:
            source_digest = _digest(source_digest, "source_digest")
        if destination is not None:
            destination = _text(destination, "destination", 4096)
        if rollback_digest is not None:
            rollback_digest = _digest(rollback_digest, "rollback_digest")
        if staging_id is not None:
            staging_id = _text(staging_id, "staging_id", 160)
        if resume_after is not None:
            try:
                resume_after = Cursor.from_wire(resume_after)
            except ValueError as exc:
                raise OperatorContractError("resume_after is invalid") from exc
        for number, name in (
            (estimated_items, "estimated_items"),
            (estimated_bytes, "estimated_bytes"),
        ):
            if number is not None and (
                isinstance(number, bool) or not isinstance(number, int) or number < 0
            ):
                raise OperatorContractError(f"{name} must be a non-negative integer")
        if not isinstance(inventory, Mapping):
            raise OperatorContractError("lifecycle inventory must be an object")
        parsed_inventory: dict[str, tuple[str, ...]] = {}
        for key, items in inventory.items():
            if (
                not isinstance(key, str)
                or not isinstance(items, list)
                or len(items) > 4096
            ):
                raise OperatorContractError("lifecycle inventory is invalid")
            parsed_inventory[key] = tuple(
                _text(item, "inventory item", 4096) for item in items
            )
        if not isinstance(exclusions, list) or not all(
            isinstance(item, str) for item in exclusions
        ):
            raise OperatorContractError("export exclusions must be an array of strings")

        if action in ("install", "update") and target_version is None:
            raise OperatorContractError(
                "install and update plans require target_version"
            )
        if action == "update" and rollback_digest is None:
            raise OperatorContractError(
                "update plan requires verified rollback material"
            )
        if action == "uninstall" and (
            disposition is None
            or "runtime" not in parsed_inventory
            or "user_data" not in parsed_inventory
        ):
            raise OperatorContractError(
                "uninstall plan requires data disposition and separate inventories"
            )
        if action in ("migrate", "restore") and (
            source_digest is None or staging_id is None
        ):
            raise OperatorContractError(
                "migration and restore require verified staged input"
            )
        parsed_exclusions = frozenset(exclusions)
        if action == "export" and (
            destination is None or not _EXPORT_EXCLUSIONS.issubset(parsed_exclusions)
        ):
            raise OperatorContractError(
                "export requires a destination and complete private/derivative exclusions"
            )
        if action == "rollback" and rollback_digest is None:
            raise OperatorContractError("rollback plan requires rollback material")

        return cls(
            action=action,
            plan=base,
            current_version=current_version,
            target_version=target_version,
            data_disposition=disposition,
            source_digest=source_digest,
            destination=destination,
            rollback_digest=rollback_digest,
            staging_id=staging_id,
            resume_after=resume_after,
            estimated_items=estimated_items,
            estimated_bytes=estimated_bytes,
            inventory=parsed_inventory,
            exclusions=parsed_exclusions,
        )


@dataclass(frozen=True, slots=True)
class LifecycleReceipt:
    receipt: ActionReceipt
    artifact_bytes: int | None
    artifact_path: str | None
    enrollment: EnrollmentReceipt | None = None

    @property
    def state(self) -> str:
        return self.receipt.state

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> LifecycleReceipt:
        raw = _object(value, "lifecycle receipt")
        receipt = ActionReceipt.from_wire(raw, expected_scope)
        artifact_bytes = raw.get("artifact_bytes")
        artifact_path = raw.get("artifact_path")
        if artifact_bytes is not None and (
            isinstance(artifact_bytes, bool)
            or not isinstance(artifact_bytes, int)
            or artifact_bytes < 0
        ):
            raise OperatorContractError("artifact_bytes must be non-negative")
        if artifact_path is not None:
            artifact_path = _text(artifact_path, "artifact_path", 4096)
        return cls(receipt, artifact_bytes, artifact_path)


@dataclass(frozen=True, slots=True)
class RecoveredLifecycle:
    receipt: LifecycleReceipt
    recovery_state: RecoveryState

    @classmethod
    def from_wire(cls, value: object, expected_scope: Scope) -> RecoveredLifecycle:
        raw = _object(value, "lifecycle recovery")
        state = raw.get("recovery_state")
        if state not in (
            "committed",
            "resumable",
            "rollback_available",
            "failed",
            "cancelled",
            "outcome_unknown",
        ):
            raise OperatorContractError("lifecycle recovery state is invalid")
        return cls(
            LifecycleReceipt.from_wire(raw.get("receipt"), expected_scope), state
        )


class HypermidOperatorLifecycle:
    def __init__(self, client: HypermidClient) -> None:
        self.client = client

    async def plan(
        self,
        action: LifecycleOperation,
        *,
        target_version: str | None = None,
        data_disposition: DataDisposition | None = None,
        source: str | None = None,
        destination: str | None = None,
        resume_after: Cursor | None = None,
        params: Mapping[str, JsonValue] | None = None,
    ) -> LifecyclePlan:
        if action not in _OPERATIONS:
            raise ValueError("unsupported lifecycle operation")
        payload: dict[str, JsonValue] = {"params": dict(params or {})}
        if target_version is not None:
            payload["target_version"] = target_version
        if data_disposition is not None:
            payload["data_disposition"] = data_disposition
        if source is not None:
            payload["source"] = source
        if destination is not None:
            payload["destination"] = destination
        if resume_after is not None:
            payload["resume_after"] = resume_after.to_wire()
        value = await self.client.request(f"lifecycle.{action}.plan", payload)
        return LifecyclePlan.from_wire(action, value, self.client.scope)

    async def apply(
        self,
        plan: LifecyclePlan,
        *,
        reviewed_digest: str,
        confirm_destructive: bool = False,
        confirm_purge: bool = False,
    ) -> LifecycleReceipt:
        if plan.scope != self.client.scope:
            raise ValueError("plan scope does not match the authenticated scope")
        if reviewed_digest != plan.plan_digest:
            raise ValueError("reviewed digest does not match the current plan")
        if plan.plan.blockers:
            raise ValueError("a blocked lifecycle plan cannot be applied")
        if plan.plan.destructive and not confirm_destructive:
            raise ValueError("destructive lifecycle operation requires confirmation")
        if plan.action == "uninstall":
            if plan.data_disposition == "purge" and not confirm_purge:
                raise ValueError("data purge requires a distinct confirmation")
            if plan.data_disposition != "purge" and confirm_purge:
                raise ValueError(
                    "retain/export plans cannot be upgraded to purge at apply"
                )
        staged_enrollment: StagedEnrollment | None = None
        if plan.action == "install":
            raw = plan.plan.raw
            plan_params = raw.get("params") if isinstance(raw, Mapping) else None
            local_enrollment = (
                plan_params.get("local_enrollment")
                if isinstance(plan_params, Mapping)
                else None
            )
            if not isinstance(local_enrollment, Mapping) or plan.target_version is None:
                raise OperatorContractError(
                    "reviewed install plan is missing exact local enrollment authority"
                )
            staged_enrollment = stage_enrollment(
                scope=plan.scope,
                reviewed_plan_digest=plan.plan_digest,
                target_version=plan.target_version,
                params=local_enrollment,
            )
        payload: dict[str, JsonValue] = {
            "plan_id": plan.plan.plan_id,
            "plan_digest": plan.plan_digest,
            "confirm_destructive": confirm_destructive,
            "confirm_purge": confirm_purge,
        }
        if plan.plan.authority_digest is not None:
            payload["authority_digest"] = plan.plan.authority_digest
        if plan.plan.blocker_digest is not None:
            payload["blocker_digest"] = plan.plan.blocker_digest
        try:
            value = await self.client.request(
                f"lifecycle.{plan.action}.apply", payload, effect_kind="durable"
            )
            receipt = LifecycleReceipt.from_wire(value, self.client.scope)
            if receipt.receipt.plan_digest != plan.plan_digest:
                raise OperatorContractError(
                    "lifecycle receipt does not bind the reviewed plan"
                )
            if plan.action == "export" and receipt.state == "committed":
                self._verify_export(plan, receipt)
            if plan.action == "restore" and receipt.state == "committed":
                if receipt.receipt.artifact_digest != plan.source_digest:
                    raise OperatorContractError(
                        "restore receipt does not bind the verified source artifact"
                    )
            if staged_enrollment is not None:
                if receipt.state == "committed":
                    enrollment = staged_enrollment.commit(
                        lifecycle_receipt=receipt.receipt
                    )
                    return LifecycleReceipt(
                        receipt.receipt,
                        receipt.artifact_bytes,
                        receipt.artifact_path,
                        enrollment,
                    )
                staged_enrollment.abort()
            return receipt
        except BaseException:
            if staged_enrollment is not None:
                staged_enrollment.abort()
            raise

    async def status(self, job_id: str) -> LifecycleReceipt:
        value = await self.client.request("lifecycle.status", {"job_id": job_id})
        return LifecycleReceipt.from_wire(value, self.client.scope)

    async def recover(self, job_id: str) -> RecoveredLifecycle:
        value = await self.client.request("lifecycle.recover", {"job_id": job_id})
        return RecoveredLifecycle.from_wire(value, self.client.scope)

    async def resume(
        self, job_id: str, *, after: Cursor | None = None
    ) -> LifecycleReceipt:
        payload: dict[str, JsonValue] = {"job_id": job_id}
        if after is not None:
            payload["after"] = after.to_wire()
        value = await self.client.request(
            "lifecycle.resume", payload, effect_kind="idempotent"
        )
        return LifecycleReceipt.from_wire(value, self.client.scope)

    @staticmethod
    def _verify_export(plan: LifecyclePlan, receipt: LifecycleReceipt) -> None:
        digest = receipt.receipt.artifact_digest
        if digest is None or receipt.artifact_bytes is None:
            raise OperatorContractError(
                "committed export is missing digest or byte-count evidence"
            )
        destination = receipt.artifact_path or plan.destination
        if destination is None:
            raise OperatorContractError("committed export is missing its artifact path")
        artifact = Path(destination)
        if not artifact.is_file() or artifact.is_symlink():
            raise OperatorContractError("export artifact is not a regular file")
        hasher = hashlib.sha256()
        total = 0
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                hasher.update(chunk)
        if total != receipt.artifact_bytes or hasher.hexdigest() != digest:
            raise OperatorContractError("export artifact verification failed")


__all__ = [
    "HypermidOperatorLifecycle",
    "LifecyclePlan",
    "LifecycleReceipt",
    "RecoveredLifecycle",
    "EnrollmentReceipt",
]
