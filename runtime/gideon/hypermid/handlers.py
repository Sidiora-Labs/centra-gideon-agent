from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .operator_lifecycle import LifecycleOperation

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .operations import PlanStep

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .security_operations import SecurityOperations
    from .local_install import LocalInstallAuthority
    from .lifecycle import HypermidLifecycle
    from .status import HypermidStatus

from dataclasses import asdict
from typing import Any, Callable, Mapping

from .foundation import Cursor
from .operations import (
    ActionPlan,
    ActionReceipt,
    DiagnosticsSnapshot,
    HypermidOperations,
)
from .operator_lifecycle import (
    HypermidOperatorLifecycle,
    LifecyclePlan,
    LifecycleReceipt,
    RecoveredLifecycle,
)

_LOCAL_BACKUP_CREDENTIAL_REF = "local-backup"


class HypermidHandlerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message[:512]
        super().__init__(self.message)


def _safe_error(error: BaseException) -> HypermidHandlerError:
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not code or len(code) > 64:
        code = "HYPERMID_OPERATION_FAILED"
    message = str(error).strip() or "Hypermid operation failed"
    return HypermidHandlerError(code, message)


def _step(step: PlanStep) -> dict[str, Any]:
    value = asdict(step)
    return {key: child for key, child in value.items() if child is not None}


def _receipt(value: LifecycleReceipt) -> dict[str, Any]:
    receipt = value.receipt
    result: dict[str, Any] = {
        "job_id": receipt.job_id,
        "operation": receipt.operation,
        "scope": receipt.scope.to_wire(),
        "plan_digest": receipt.plan_digest,
        "state": receipt.state,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
        "cursor": receipt.cursor.to_wire() if receipt.cursor is not None else None,
        "steps": [_step(step) for step in receipt.steps],
        "rollback_available": receipt.rollback_available,
        "artifact_digest": receipt.artifact_digest,
        "error": receipt.error,
    }
    if value.artifact_bytes is not None:
        result["artifact_bytes"] = value.artifact_bytes
    if value.artifact_path is not None:
        result["artifact_path"] = value.artifact_path
    return result


def _diagnostics(value: DiagnosticsSnapshot) -> dict[str, Any]:
    return {
        "scope": value.scope.to_wire(),
        "observed_at": value.observed_at,
        "cursor": value.cursor.to_wire(),
        "cached": value.cached,
        "checks": [
            {key: child for key, child in asdict(check).items() if child is not None}
            for check in value.checks
        ],
    }


def _action_receipt(receipt: ActionReceipt) -> dict[str, Any]:
    return {
        "job_id": receipt.job_id,
        "operation": receipt.operation,
        "scope": receipt.scope.to_wire(),
        "plan_digest": receipt.plan_digest,
        "state": receipt.state,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
        "cursor": receipt.cursor.to_wire() if receipt.cursor is not None else None,
        "steps": [_step(step) for step in receipt.steps],
        "rollback_available": receipt.rollback_available,
        "artifact_digest": receipt.artifact_digest,
        "error": receipt.error,
    }


class HypermidHandlers:
    """Async facade consumed by native CLI and dashboard route handlers."""

    def __init__(
        self,
        operations: HypermidOperations,
        lifecycle: HypermidOperatorLifecycle,
        *,
        local_status: Callable[[], HypermidStatus] | None = None,
        security_operations: SecurityOperations | None = None,
        security_owner: HypermidLifecycle | None = None,
        local_install_authority: LocalInstallAuthority | None = None,
    ) -> None:
        self.operations = operations
        self.lifecycle = lifecycle
        self.local_status = local_status
        self.security_operations = security_operations
        self.security_owner = security_owner
        self.local_install_authority = local_install_authority
        self._plans: dict[
            str, tuple[LocalInstallAuthority | HypermidOperatorLifecycle, LifecyclePlan]
        ] = {}
        self._maintenance_plans: dict[str, ActionPlan] = {}

    async def status(self) -> Mapping[str, Any]:
        local: dict[str, Any] = {}
        if self.local_status is not None:
            snapshot = self.local_status()
            local = (
                snapshot.to_dict()
                if hasattr(snapshot, "to_dict")
                else dict(cast(Mapping[str, object], snapshot))
            )
        try:
            remote = dict(await self.operations.status())
        except Exception as error:
            if local:
                local["recovery_state"] = "daemon_unavailable"
                local["error"] = {
                    "code": getattr(error, "code", "DAEMON_UNAVAILABLE"),
                    "message": str(error)[:240],
                }
                return local
            raise _safe_error(error) from error
        result = dict(local)
        result.update(remote)
        return result

    async def doctor(self, *, refresh: bool = False) -> Mapping[str, Any]:
        try:
            return _diagnostics(await self.operations.diagnostics(refresh=refresh))
        except Exception as error:
            raise _safe_error(error) from error

    async def logs(
        self,
        *,
        after: Mapping[str, Any] | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int = 200,
    ) -> Mapping[str, Any]:
        try:
            page = await self.operations.logs(
                after=Cursor.from_wire(after) if after is not None else None,
                filters=filters,
                limit=limit,
            )
            return {
                "scope": page.scope.to_wire(),
                "entries": [
                    {
                        **asdict(entry),
                        "cursor": entry.cursor.to_wire(),
                    }
                    for entry in page.entries
                ],
                "cursor": page.cursor.to_wire(),
                "gap": page.gap,
                "recovery_cursor": (
                    page.recovery_cursor.to_wire()
                    if page.recovery_cursor is not None
                    else None
                ),
            }
        except Exception as error:
            raise _safe_error(error) from error

    async def plan_maintenance(
        self, action: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            plan = await self.operations.plan_maintenance(
                action, params=payload.get("params") or {}
            )
            self._maintenance_plans[plan.plan_id] = plan
            if not isinstance(plan.raw, Mapping):
                raise HypermidHandlerError(
                    "INVALID_PLAN", "daemon returned no reviewable plan"
                )
            return plan.raw
        except HypermidHandlerError:
            raise
        except Exception as error:
            raise _safe_error(error) from error

    async def apply_maintenance(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        plan_id = payload.get("plan_id")
        plan_digest = payload.get("plan_digest")
        if not isinstance(plan_id, str) or not isinstance(plan_digest, str):
            raise HypermidHandlerError(
                "INVALID_REQUEST", "plan_id and plan_digest are required"
            )
        plan = self._maintenance_plans.get(plan_id)
        if plan is None:
            raise HypermidHandlerError(
                "PLAN_NOT_FOUND", "reviewed maintenance plan is not available"
            )
        try:
            receipt = await self.operations.apply_maintenance(
                plan,
                reviewed_digest=plan_digest,
                confirm_destructive=payload.get("confirm_destructive") is True,
            )
            if receipt.terminal:
                self._maintenance_plans.pop(plan_id, None)
            return _action_receipt(receipt)
        except Exception as error:
            raise _safe_error(error) from error

    async def maintenance_status(self, job_id: str) -> Mapping[str, Any]:
        try:
            return _action_receipt(await self.operations.maintenance_status(job_id))
        except Exception as error:
            raise _safe_error(error) from error

    async def cancel_maintenance(self, job_id: str) -> Mapping[str, Any]:
        try:
            return _action_receipt(await self.operations.cancel_maintenance(job_id))
        except Exception as error:
            raise _safe_error(error) from error

    def _security(self) -> SecurityOperations:
        if self.security_operations is None and self.security_owner is not None:
            from .credential_authority import attach_security_operations

            self.security_operations = attach_security_operations(self.security_owner)
        if self.security_operations is None:
            raise HypermidHandlerError(
                "SECURITY_OPERATIONS_UNAVAILABLE",
                "Hypermid security operations are not configured",
            )
        return self.security_operations

    def _security_payload(
        self, action: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if action not in {"backup", "restore"} or self.security_owner is None:
            return payload
        if "credential_handle" in payload:
            raise HypermidHandlerError(
                "INVALID_REQUEST", "credential handles are selected by the server"
            )
        credential_ref = payload.get("credential_ref", _LOCAL_BACKUP_CREDENTIAL_REF)
        if credential_ref != _LOCAL_BACKUP_CREDENTIAL_REF:
            raise HypermidHandlerError(
                "SECURITY_CREDENTIAL_NOT_FOUND",
                "selected security credential is unavailable",
            )
        handles = getattr(self.security_owner, "security_credential_handles", None)
        selected = handles.get(action) if isinstance(handles, Mapping) else None
        if not isinstance(selected, str):
            raise HypermidHandlerError(
                "SECURITY_CREDENTIAL_UNAVAILABLE",
                "no credential is configured for this security operation",
            )
        return dict(payload) | {
            "credential_ref": credential_ref,
            "credential_handle": selected,
        }

    async def security_credentials(self) -> Mapping[str, Any]:
        service = self._security()
        handles = getattr(self.security_owner, "security_credential_handles", None)
        configured = isinstance(handles, Mapping)
        if configured:
            from .network_policy import SecretHandle

            for action in ("backup", "restore"):
                handle = cast(Mapping[str, object], handles).get(action)
                if not isinstance(handle, str):
                    configured = False
                    break
                try:
                    valid = service.secret_vault.dispatch_with_secret(
                        SecretHandle(handle),
                        principal_id=service.principal_id,
                        operation=f"hypermid.{action}",
                        dispatch=lambda material: len(material) == 32,
                    )
                except Exception:
                    configured = False
                    break
                if not valid:
                    configured = False
                    break
        return {
            "credentials": [
                {
                    "credential_ref": _LOCAL_BACKUP_CREDENTIAL_REF,
                    "label": "Local backup key",
                    "purposes": ["backup", "restore"],
                    "configured": configured,
                }
            ]
        }

    async def security_plan(
        self, action: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            service = self._security()
            return await service.plan(
                action,
                self._security_payload(action, payload),
                self.lifecycle.client.scope,
            )
        except HypermidHandlerError:
            raise
        except Exception as error:
            raise _safe_error(error) from error

    async def security_apply(
        self, action: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            return await self._security().apply(
                action, payload, self.lifecycle.client.scope
            )
        except HypermidHandlerError:
            raise
        except Exception as error:
            raise _safe_error(error) from error

    async def security_status(self, job_id: str) -> Mapping[str, Any]:
        try:
            return await self._security().status(job_id, self.lifecycle.client.scope)
        except HypermidHandlerError:
            raise
        except Exception as error:
            raise _safe_error(error) from error

    async def security_recover(self, job_id: str) -> Mapping[str, Any]:
        try:
            return await self._security().recover(job_id, self.lifecycle.client.scope)
        except HypermidHandlerError:
            raise
        except Exception as error:
            raise _safe_error(error) from error

    async def plan(self, action: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            after = payload.get("resume_after")
            authority = self._lifecycle_authority(action)
            plan = await authority.plan(
                cast("LifecycleOperation", action),
                target_version=payload.get("target_version"),
                data_disposition=payload.get("data_disposition"),
                source=payload.get("source"),
                destination=payload.get("destination"),
                resume_after=Cursor.from_wire(after) if after is not None else None,
                params=payload.get("params") or {},
            )
            self._plans[plan.plan.plan_id] = (authority, plan)
            raw = plan.plan.raw
            if not isinstance(raw, Mapping):
                raise HypermidHandlerError(
                    "INVALID_PLAN", "daemon returned no reviewable plan"
                )
            return raw
        except HypermidHandlerError:
            raise
        except Exception as error:
            raise _safe_error(error) from error

    async def apply(self, action: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        plan_id = payload.get("plan_id")
        plan_digest = payload.get("plan_digest")
        if not isinstance(plan_id, str) or not isinstance(plan_digest, str):
            raise HypermidHandlerError(
                "INVALID_REQUEST", "plan_id and plan_digest are required"
            )
        selected = self._plans.get(plan_id)
        if selected is None or selected[1].action != action:
            raise HypermidHandlerError(
                "PLAN_NOT_FOUND", "reviewed lifecycle plan is not available"
            )
        authority, plan = selected
        try:
            receipt = await authority.apply(
                plan,
                reviewed_digest=plan_digest,
                confirm_destructive=payload.get("confirm_destructive") is True,
                confirm_purge=payload.get("confirm_purge") is True,
            )
            if receipt.receipt.terminal:
                self._plans.pop(plan_id, None)
            return _receipt(receipt)
        except Exception as error:
            raise _safe_error(error) from error

    def _lifecycle_authority(
        self, action: str
    ) -> LocalInstallAuthority | HypermidOperatorLifecycle:
        if (
            action == "install"
            and self.local_install_authority is not None
            and (
                not self.lifecycle.client.connected
                or getattr(self.security_owner, "process", None) is not None
            )
        ):
            return self.local_install_authority
        return self.lifecycle

    async def job_status(self, job_id: str) -> Mapping[str, Any]:
        try:
            return _receipt(await self.lifecycle.status(job_id))
        except Exception as error:
            raise _safe_error(error) from error

    async def recover(self, job_id: str) -> Mapping[str, Any]:
        try:
            recovered: RecoveredLifecycle = await self.lifecycle.recover(job_id)
            return {
                "receipt": _receipt(recovered.receipt),
                "recovery_state": recovered.recovery_state,
            }
        except Exception as error:
            raise _safe_error(error) from error

    async def resume(
        self, job_id: str, after: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        try:
            cursor = Cursor.from_wire(after) if after is not None else None
            return _receipt(await self.lifecycle.resume(job_id, after=cursor))
        except Exception as error:
            raise _safe_error(error) from error

    async def dispatch(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if operation == "status":
            return await self.status()
        if operation == "doctor":
            return await self.doctor(refresh=payload.get("refresh") is True)
        if operation == "diagnostics.get":
            return await self.doctor(refresh=False)
        if operation == "diagnostics.rerun":
            return await self.doctor(refresh=True)
        if operation == "logs.read":
            after = payload.get("after")
            filters = payload.get("filters")
            return await self.logs(
                after=after if isinstance(after, Mapping) else None,
                filters=filters if isinstance(filters, Mapping) else None,
                limit=int(payload.get("limit", 200)),
            )
        if operation == "maintenance.plan":
            return await self.plan_maintenance(str(payload.get("action", "")), payload)
        if operation == "maintenance.apply":
            return await self.apply_maintenance(payload)
        if operation == "maintenance.status":
            return await self.maintenance_status(str(payload.get("job_id", "")))
        if operation == "maintenance.cancel":
            return await self.cancel_maintenance(str(payload.get("job_id", "")))
        if operation.startswith("security."):
            parts = operation.split(".")
            if operation == "security.credentials":
                return await self.security_credentials()
            if len(parts) == 3 and parts[2] == "plan":
                return await self.security_plan(parts[1], payload)
            if len(parts) == 3 and parts[2] == "apply":
                return await self.security_apply(parts[1], payload)
            if operation == "security.status":
                return await self.security_status(str(payload.get("job_id", "")))
            if operation == "security.recover":
                return await self.security_recover(str(payload.get("job_id", "")))
        if operation.startswith("lifecycle."):
            parts = operation.split(".")
            if len(parts) == 3 and parts[2] == "plan":
                return await self.plan(parts[1], payload)
            if len(parts) == 3 and parts[2] == "apply":
                return await self.apply(parts[1], payload)
            if operation == "lifecycle.status":
                return await self.job_status(str(payload.get("job_id", "")))
            if operation == "lifecycle.recover":
                return await self.recover(str(payload.get("job_id", "")))
            if operation == "lifecycle.resume":
                after = payload.get("after")
                return await self.resume(
                    str(payload.get("job_id", "")),
                    after if isinstance(after, Mapping) else None,
                )
        raise HypermidHandlerError(
            "UNSUPPORTED_OPERATION", "Hypermid operation is unsupported"
        )


def service_for(owner: HypermidLifecycle) -> HypermidHandlers:
    existing = getattr(owner, "handlers", None)
    if isinstance(existing, HypermidHandlers):
        existing.security_owner = owner
        security_operations = getattr(owner, "security_operations", None)
        if security_operations is not None:
            existing.security_operations = security_operations
        return existing
    adapter = getattr(owner, "adapter", None)
    client = getattr(adapter, "client", None)
    if client is None:
        raise HypermidHandlerError(
            "NOT_CONFIGURED", "Hypermid client is not configured"
        )
    local_install = getattr(owner, "local_install_authority", None)
    if local_install is None:
        daemon_config = getattr(owner, "daemon_config", None)
        executable = getattr(daemon_config, "executable", None)
        if executable:
            from .enrollment_source import native_enrollment_source
            from .local_install import LocalInstallAuthority

            local_install = LocalInstallAuthority(
                owner,
                executable=executable,
                scope=client.scope,
                enrollment_source=native_enrollment_source(),
            )
            setattr(owner, "local_install_authority", local_install)
    handlers = HypermidHandlers(
        HypermidOperations(client),
        HypermidOperatorLifecycle(client),
        local_status=getattr(adapter, "status", None),
        security_operations=getattr(owner, "security_operations", None),
        security_owner=owner,
        local_install_authority=local_install,
    )
    setattr(owner, "handlers", handlers)
    return handlers


__all__ = ["HypermidHandlerError", "HypermidHandlers", "service_for"]
