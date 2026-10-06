"""Local Hypermid daemon supervision for the Gideon runtime."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import shutil
import stat
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Mapping

from .adapter import HypermidAdapter
from .client import HypermidClient
from .modules import HypermidModuleSupervisor
from .foundation import Id
from .models import MAX_SAFE_INTEGER, Scope
from .status import HypermidStatus

log = logging.getLogger(__name__)


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


_CAPABILITY_OPERATIONS = frozenset(
    {
        "read",
        "append",
        "revise",
        "archive",
        "delete",
        "export",
        "restore",
        "administer",
        "model-use",
        "network-use",
        "artifact-install",
        "artifact-update",
    }
)


@dataclass(frozen=True, slots=True)
class LocalEnrollment:
    """Explicit operator grant used to enroll one local daemon principal."""

    scope: Scope
    credential_id: Id
    capability_id: Id
    operations: tuple[str, ...]
    resources: tuple[Id, ...]
    expires_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "credential_id", Id(self.credential_id))
        object.__setattr__(self, "capability_id", Id(self.capability_id))
        operations = tuple(dict.fromkeys(self.operations))
        resources = tuple(dict.fromkeys(Id(item) for item in self.resources))
        if not operations or any(item not in _CAPABILITY_OPERATIONS for item in operations):
            raise ValueError("Hypermid enrollment operations are invalid")
        if not resources:
            raise ValueError("Hypermid enrollment requires at least one resource")
        if (
            isinstance(self.expires_ms, bool)
            or not isinstance(self.expires_ms, int)
            or not 1 <= self.expires_ms <= MAX_SAFE_INTEGER
        ):
            raise ValueError("Hypermid enrollment expiry is invalid")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "resources", resources)

    def require_live(self, scope: Scope) -> None:
        if self.scope != scope:
            raise PermissionError("Hypermid enrollment scope does not match this runtime")
        if self.expires_ms <= time.time_ns() // 1_000_000:
            raise PermissionError("Hypermid enrollment has expired")

    @classmethod
    def load(cls, path: str | Path, *, scope: Scope) -> LocalEnrollment:
        enrollment_path = Path(path)
        info = enrollment_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise PermissionError("Hypermid enrollment must be a regular file")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise PermissionError("Hypermid enrollment has the wrong owner")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError("Hypermid enrollment must have mode 0600")
        raw = json.loads(enrollment_path.read_text(encoding="utf-8"))
        expected = {
            "scope",
            "credential_id",
            "capability_id",
            "operations",
            "resources",
            "expires_ms",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("Hypermid enrollment fields are invalid")
        operations = raw["operations"]
        resources = raw["resources"]
        if not isinstance(operations, list) or not all(
            isinstance(item, str) for item in operations
        ):
            raise ValueError("Hypermid enrollment operations are invalid")
        if not isinstance(resources, list) or not all(
            isinstance(item, str) for item in resources
        ):
            raise ValueError("Hypermid enrollment resources are invalid")
        enrollment = cls(
            scope=Scope.from_wire(raw["scope"]),
            credential_id=Id(raw["credential_id"]),
            capability_id=Id(raw["capability_id"]),
            operations=tuple(operations),
            resources=tuple(Id(item) for item in resources),
            expires_ms=raw["expires_ms"],
        )
        enrollment.require_live(scope)
        return enrollment


class HypermidLifecycle:
    """Own a daemon subprocess only when this runtime started it."""

    def __init__(
        self,
        adapter: HypermidAdapter,
        daemon_config: object,
        *,
        connection_record: str | Path,
        module_supervisor: HypermidModuleSupervisor | None = None,
        enrollment: LocalEnrollment | None = None,
        config_store: object | None = None,
        initial_config: object | None = None,
        runtime: Any | None = None,
    ) -> None:
        self.adapter = adapter
        self.daemon_config = daemon_config
        self.connection_record = Path(connection_record)
        self.module_supervisor = module_supervisor
        self.enrollment = enrollment
        self.config_store = config_store
        self.initial_config = initial_config
        self.runtime = runtime
        self.process: asyncio.subprocess.Process | None = None
        self.writer: object | None = None
        self.primary_engine: object | None = None
        self.context_bridge: object | None = None
        self._host_engine: object | None = None
        self._runtime_mode = "off"
        self._turn_hook = self._turn_boundary
        self._boundary_lock = asyncio.Lock()
        self._reconciled_daemon_id: str | None = None
        self._inspection_lock = threading.Lock()
        self._primary_context_inspections: OrderedDict[str, dict[str, object]] = (
            OrderedDict()
        )

    async def start(self) -> HypermidStatus:
        from gideon.cognition.context_engine import get_engine, set_turn_boundary_hook

        self._host_engine = get_engine()
        self.adapter.set_delegate(self._host_engine)
        if not set_turn_boundary_hook(self._turn_hook, expected=None):
            raise RuntimeError("another context turn-boundary authority is already active")
        try:
            desired = self._configured_mode()
            return await self._apply_mode(desired, activate_primary=False)
        except Exception as error:
            self.adapter.mark_unavailable(error, code="RUNTIME_START_FAILED")
            log.warning("Hypermid runtime start failed closed: %s", error)
            return self.adapter.status()

    async def reviewed_enrollment_restart(self, enrollment: LocalEnrollment, *, verify_current, finalize):
        """Drain an owned daemon, verify its reviewed grant, then publish it."""
        async with self._boundary_lock:
            process = self.process
            previous = self.enrollment
            if process is None or process.returncode is not None or previous is None:
                raise PermissionError("reviewed enrollment refresh requires an owned running daemon")
            if (enrollment.scope, enrollment.credential_id, enrollment.capability_id) != (previous.scope, previous.credential_id, previous.capability_id):
                raise PermissionError("reviewed enrollment refresh cannot change runtime identity")
            desired = self._configured_mode()
            if _value(desired) == "off":
                raise PermissionError("reviewed enrollment refresh requires an active runtime")
            verify_current()
            await self._deactivate_writer()
            try:
                await self._disconnect()
                self.enrollment = enrollment
                status = await self._apply_mode(desired, activate_primary=False)
                if not self._reviewed_restart_ready(status):
                    raise RuntimeError("reviewed Hypermid enrollment did not become ready")
                await self._validate_enrollment_grants()
                return finalize()
            except BaseException as original:
                try:
                    await self._deactivate_writer()
                    await self._disconnect()
                    self.enrollment = previous
                    status = await self._apply_mode(desired, activate_primary=False)
                    if not self._reviewed_restart_ready(status):
                        raise RuntimeError("previous Hypermid enrollment did not become ready")
                except BaseException as rollback:
                    self.enrollment = previous
                    raise BaseExceptionGroup("reviewed enrollment failed and runtime rollback is unresolved", [original, rollback])
                raise

    def _reviewed_restart_ready(self, status: HypermidStatus) -> bool:
        # Primary activation remains a turn-boundary operation after restart.
        return (status.available and status.scope_bound and status.digest_health == "healthy"
                and (status.healthy or (status.mode == "primary" and self.writer is not None
                     and status.writer == "gideon" and status.lease_state == "none")))

    async def _validate_enrollment_grants(self) -> None:
        from .contracts import AccessRequest, GrantOperation
        from .memory import _trace
        from .memory_client import MemoryClient
        import secrets
        client = self.adapter.client
        enrollment = self.enrollment
        if client is None or enrollment is None or not client.connected:
            raise PermissionError("reviewed enrollment has no authenticated daemon")
        memory = MemoryClient(client, capability_id=enrollment.capability_id)
        for resource in ("memory-records", "memory-embedding"):
            if Id(resource) not in enrollment.resources:
                continue
            request = AccessRequest(GrantOperation.READ, client.scope, client.scope, Id(resource), _trace())
            if resource == "memory-embedding":
                await memory.embedding_active(request)
            else:
                try:
                    await memory.list(request, limit=1)
                except Exception as error:
                    if getattr(getattr(error, "error", None), "code", None) != "SCOPE_NOT_FOUND":
                        raise
        if "administer" in enrollment.operations and Id("memory-service") in enrollment.resources:
            # A fresh origin has no derived scope; retirement checks administration without issuing one.
            await memory._call("memory.private-scope.retire", {"origin_session_key": "enrollment-check:" + secrets.token_hex(24)}, trace=_trace(), durable=True)

    async def stop(self) -> None:
        from gideon.cognition.context_engine import set_turn_boundary_hook

        set_turn_boundary_hook(None, expected=self._turn_hook)
        try:
            await self._deactivate_writer()
        except Exception:
            log.error("Hypermid writer handback is unresolved during shutdown", exc_info=True)
        await self._disconnect()
        store, self.config_store = self.config_store, None
        if store is not None:
            store.close()

    @property
    def capability_id(self) -> Id | None:
        return None if self.enrollment is None else self.enrollment.capability_id

    def primary_context_inspection(self, session_id: str) -> dict[str, object]:
        with self._inspection_lock:
            inspection = self._primary_context_inspections.get(session_id)
            if inspection is None:
                raise KeyError(session_id)
            self._primary_context_inspections.move_to_end(session_id)
            return copy.deepcopy(inspection)

    def _record_primary_context_inspection(
        self, session_id: str, inspection: Mapping[str, object]
    ) -> None:
        with self._inspection_lock:
            self._primary_context_inspections[session_id] = copy.deepcopy(dict(inspection))
            self._primary_context_inspections.move_to_end(session_id)
            while len(self._primary_context_inspections) > 128:
                self._primary_context_inspections.popitem(last=False)

    def _configured_mode(self) -> object:
        store = self.config_store
        if store is None:
            return self.adapter.mode
        if self.initial_config is not None:
            store.seed_if_empty(self.initial_config)
        return store.snapshot().config.mode

    async def _turn_boundary(self, session_key: str) -> None:
        del session_key
        from gideon.cognition.context_engine import ContextBoundaryRefusal

        async with self._boundary_lock:
            store = self.config_store
            if store is not None:
                store.apply_at_turn_boundary()
                desired = store.snapshot().config.mode
            else:
                desired = self.adapter.mode
            try:
                await self._apply_mode(desired, activate_primary=True)
            except ContextBoundaryRefusal:
                raise
            except Exception as error:
                desired_value = _value(desired)
                self.adapter.mark_unavailable(error, code="AUTHORITY_REJECTED")
                if desired_value == "primary":
                    raise ContextBoundaryRefusal(
                        "Hypermid primary authority is unavailable for this turn"
                    ) from error
                log.warning(
                    "Hypermid %s transition failed; Gideon remains authoritative: %s",
                    desired_value,
                    error,
                )

    async def _apply_mode(
        self, mode: object, *, activate_primary: bool
    ) -> HypermidStatus:
        from gideon.cognition.context_engine import ContextBoundaryRefusal

        desired = _value(mode)
        if desired not in {"off", "pass_through", "shadow", "primary"}:
            raise ValueError("unsupported Hypermid mode")
        if self._runtime_mode == "primary" and desired != "primary":
            await self._deactivate_writer()
        self.adapter.set_mode(desired)
        if desired == "off":
            await self._disconnect()
            self._runtime_mode = "off"
            return self.adapter.set_mode("off")
        status = await self._ensure_transport()
        if not status.available:
            if desired == "primary" and activate_primary:
                raise ContextBoundaryRefusal(
                    "Hypermid primary transport is unavailable for this turn"
                )
            return status
        await self._ensure_writer(desired)
        self._install_adapter()
        writer = self.writer
        if writer is not None:
            writer.mode = desired
        if desired == "primary" and activate_primary:
            if writer is None:
                raise ContextBoundaryRefusal(
                    "Hypermid writer authority is unavailable for this turn"
                )
            try:
                self._require_primary_enrollment()
                if writer.snapshot().lease_state == "unknown":
                    await writer.reconcile_unknown()
                snapshot = await writer.activate_primary()
                await writer.verify_durable_status()
            except Exception as error:
                self.adapter.mark_unavailable(error, code="AUTHORITY_REJECTED")
                raise ContextBoundaryRefusal(
                    "Hypermid primary writer lease was rejected for this turn"
                ) from error
            status = self.adapter.apply_writer_snapshot(snapshot)
        elif writer is not None:
            status = self.adapter.apply_writer_snapshot(writer.snapshot())
        self._runtime_mode = desired
        return status

    def _require_primary_enrollment(self) -> None:
        enrollment = self.enrollment
        client = self.adapter.client
        if enrollment is None or client is None:
            raise PermissionError("Hypermid primary enrollment is unavailable")
        enrollment.require_live(client.scope)
        if "read" not in enrollment.operations or Id("memory-list") not in enrollment.resources:
            raise PermissionError(
                "Hypermid primary enrollment lacks summary read authority"
            )

    async def _ensure_transport(self) -> HypermidStatus:
        if self.adapter.status().available and self.adapter.client is not None:
            if self.adapter.client.connected:
                return self.adapter.status()
        if not self.connection_record.is_file():
            if not bool(getattr(self.daemon_config, "start_on_demand", False)):
                return await self.adapter.start()
            try:
                self._prepare_local_paths()
                self.process = await asyncio.create_subprocess_exec(
                    *self._command(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                )
                await self._wait_for_connection_record()
            except Exception as error:
                await self._retire_process()
                return self.adapter.mark_unavailable(
                    error, code="DAEMON_START_FAILED"
                )
        status = await self.adapter.start()
        if not status.available:
            await self._retire_process()
        elif self.module_supervisor is not None:
            self.module_supervisor.enable()
        return status

    async def _ensure_writer(self, mode: str) -> None:
        client = self.adapter.client
        if client is None or not client.connected:
            raise RuntimeError("authenticated Hypermid client is unavailable")
        daemon_id = client.daemon_instance_id
        if self.writer is not None and self._reconciled_daemon_id == daemon_id:
            self.writer.mode = mode
            await self.writer.verify_durable_status()
            return
        runtime = self.runtime
        if runtime is None:
            if mode == "primary":
                raise RuntimeError(
                    "Gideon runtime services are unavailable for primary writer reconciliation"
                )
            return
        context_builder = getattr(runtime, "ctx_builder", None)
        conversation_log = getattr(runtime, "conv_log", None)
        consolidator = getattr(runtime, "consolidator", None)
        memory = getattr(context_builder, "memory", None)
        if any(item is None for item in (context_builder, conversation_log, consolidator, memory)):
            raise RuntimeError("Gideon conversation services are incomplete")

        from gideon.cognition.memory_service import service_for
        from gideon.hypermid.authority_operations import DaemonWriterLeaseAuthority
        from gideon.hypermid.background_coordinator import (
            quiesce_summary_work,
            resume_summary_work,
        )
        from gideon.hypermid.context import ConversationContextBridge
        from gideon.hypermid.primary_engine import (
            PrimaryContextEngine,
            ThreadedPrimaryBridge,
        )
        from gideon.hypermid.writer import GideonCutoverHooks, WriterCoordinator

        scope = client.scope
        context_root = self.connection_record.parent / "context"
        context_bridge = ConversationContextBridge(
            conversation_log, context_root, scope=scope
        )

        def session_keys() -> tuple[str, ...]:
            return tuple(
                str(item["key"])
                for item in conversation_log.list_sessions()
                if isinstance(item, dict) and item.get("key")
            )

        hooks = GideonCutoverHooks(
            log=conversation_log,
            context=context_bridge,
            consolidator=consolidator,
            memory_flush=service_for(memory).flush_for_cutover,
            summary_quiesce=partial(quiesce_summary_work, timeout=3.0),
            summary_resume=resume_summary_work,
            install_writer=self._install_primary_engine,
            uninstall_writer=self._uninstall_primary_engine,
            session_keys=session_keys,
        )
        candidate = WriterCoordinator(
            mode=mode,
            scope=scope,
            authority=DaemonWriterLeaseAuthority(client),
            hooks=hooks,
        )
        await candidate.reconcile_startup()
        primary = PrimaryContextEngine(
            ThreadedPrimaryBridge(self.connection_record, scope),
            writer=candidate,
            context_bridge=context_bridge,
            summary_capability_id=self.capability_id,
            inspection_sink=self._record_primary_context_inspection,
        )
        previous = self.primary_engine
        self.context_bridge = context_bridge
        self.primary_engine = primary
        self.writer = candidate
        self._reconciled_daemon_id = daemon_id
        if previous is not None and previous is not primary:
            previous.close()

    def _install_adapter(self) -> None:
        from gideon.cognition.context_engine import compare_and_set_engine, get_engine

        current = get_engine()
        if current is self.adapter or current is self.primary_engine:
            return
        expected = self._host_engine
        if current is not expected or not compare_and_set_engine(current, self.adapter):
            raise RuntimeError("active context engine changed before Hypermid installation")

    def _install_primary_engine(self, _lease: object) -> None:
        from gideon.cognition.context_engine import compare_and_set_engine

        primary = self.primary_engine
        if primary is None or not compare_and_set_engine(self.adapter, primary):
            raise RuntimeError("active context engine changed before primary cutover")

    def _uninstall_primary_engine(self) -> None:
        from gideon.cognition.context_engine import compare_and_set_engine, get_engine

        primary = self.primary_engine
        current = get_engine()
        if primary is not None and current is primary:
            if not compare_and_set_engine(primary, self.adapter):
                raise RuntimeError("primary context engine could not be withdrawn")
        elif current not in (self.adapter, self._host_engine):
            raise RuntimeError("active context engine changed before writer handback")

    async def _deactivate_writer(self) -> None:
        writer = self.writer
        if writer is None:
            return
        if writer.snapshot().lease_state == "unknown":
            await writer.reconcile_unknown()
        snapshot = await writer.deactivate()
        self.adapter.apply_writer_snapshot(snapshot)

    async def _disconnect(self) -> None:
        from gideon.cognition.context_engine import compare_and_set_engine, get_engine

        current = get_engine()
        if current is self.primary_engine:
            raise RuntimeError("primary engine cannot disconnect before writer handback")
        if current is self.adapter:
            replacement = self._host_engine
            if replacement is None or not compare_and_set_engine(self.adapter, replacement):
                raise RuntimeError("Hypermid adapter could not restore the host engine")
        if self.module_supervisor is not None:
            await self.module_supervisor.disable()
        await self.adapter.stop()
        owned = self.process is not None
        await self._retire_process()
        if owned:
            self._remove_owned_paths()
        primary, self.primary_engine = self.primary_engine, None
        if primary is not None:
            primary.close()
        self.writer = None
        self.context_bridge = None
        self._reconciled_daemon_id = None

    def _command(self) -> tuple[str, ...]:
        executable = str(getattr(self.daemon_config, "executable", "") or "")
        endpoint = str(getattr(self.daemon_config, "endpoint", "") or "")
        if not executable:
            raise ValueError("Hypermid daemon executable is not configured")
        transport = _value(getattr(self.daemon_config, "transport", ""))
        if transport not in ("unix_socket", "loopback_tcp"):
            raise ValueError("Hypermid daemon transport is not local")
        if os.name != "nt" and transport != "unix_socket":
            raise ValueError("Hypermid daemon requires a Unix socket on this platform")
        if os.name == "nt" and transport != "loopback_tcp":
            raise ValueError("Hypermid daemon requires loopback TCP on this platform")
        if not endpoint:
            raise ValueError("Hypermid daemon socket is not configured")
        client = self.adapter.client
        if client is None:
            raise ValueError("Hypermid authenticated client is not configured")
        scope = client.scope
        enrollment = self.enrollment
        if enrollment is None:
            raise PermissionError("Hypermid local enrollment is not configured")
        enrollment.require_live(scope)
        command = [
            executable,
            "--socket",
            endpoint,
            "--connection-record",
            str(self.connection_record),
            "--local-credential-id",
            str(enrollment.credential_id),
            "--local-owner-id",
            str(scope.owner_id),
            "--local-project-id",
            str(scope.project_id),
            "--local-capability-id",
            str(enrollment.capability_id),
        ]
        if scope.workspace_id is not None:
            command.extend(("--local-workspace-id", str(scope.workspace_id)))
        for operation in enrollment.operations:
            command.extend(("--local-capability-operation", operation))
        for resource in enrollment.resources:
            command.extend(("--local-capability-resource", str(resource)))
        command.extend(("--local-capability-expires-ms", str(enrollment.expires_ms)))
        mcp_config = getattr(self.daemon_config, "mcp_config", None)
        if mcp_config is not None:
            path = Path(str(mcp_config))
            if not path.is_absolute():
                raise ValueError("Hypermid MCP configuration path must be absolute")
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise PermissionError("Hypermid MCP configuration must be a regular file")
            if hasattr(os, "geteuid") and details.st_uid != os.geteuid():
                raise PermissionError("Hypermid MCP configuration has the wrong owner")
            if stat.S_IMODE(details.st_mode) != 0o600:
                raise PermissionError("Hypermid MCP configuration must have mode 0600")
            command.extend(("--mcp-config", str(path)))
        return tuple(command)

    def _prepare_local_paths(self) -> None:
        transport = _value(getattr(self.daemon_config, "transport", ""))
        endpoint = Path(str(getattr(self.daemon_config, "endpoint", "")))
        if not self.connection_record.is_absolute():
            raise ValueError("Hypermid local paths must be absolute")
        if transport == "unix_socket":
            if not endpoint.is_absolute():
                raise ValueError("Hypermid local socket path must be absolute")
            endpoint.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(endpoint.parent, 0o700)
        self.connection_record.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.connection_record.parent, 0o700)

    async def _wait_for_connection_record(self) -> None:
        timeout_ms = int(
            getattr(self.daemon_config, "request_timeout_ms", 30_000) or 30_000
        )
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while not self.connection_record.is_file():
            process = self.process
            if process is not None and process.returncode is not None:
                raise RuntimeError(
                    f"Hypermid daemon exited before readiness (code {process.returncode})"
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Hypermid daemon readiness timed out")
            await asyncio.sleep(0.02)

    async def _retire_process(self) -> None:
        process, self.process = self.process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        timeout_ms = int(
            getattr(self.daemon_config, "shutdown_timeout_ms", 10_000) or 10_000
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _remove_owned_paths(self) -> None:
        tls_directory = self._owned_tls_directory()
        paths = [self.connection_record]
        if _value(getattr(self.daemon_config, "transport", "")) == "unix_socket":
            paths.append(Path(str(getattr(self.daemon_config, "endpoint", ""))))
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove retired Hypermid path %s", path)
        if tls_directory is not None:
            for name in (
                "ca.pem",
                "ca-key.pem",
                "server.pem",
                "server-key.pem",
                "server-name",
                "client.pem",
                "client-key.pem",
            ):
                try:
                    (tls_directory / name).unlink(missing_ok=True)
                except OSError:
                    log.warning("Could not remove retired Hypermid TLS material")
            try:
                tls_directory.rmdir()
            except OSError:
                log.warning("Could not remove retired Hypermid TLS directory")

    def _owned_tls_directory(self) -> Path | None:
        try:
            raw = json.loads(self.connection_record.read_text(encoding="utf-8"))
            certificate = raw.get("ca_certificate")
            if not isinstance(certificate, str):
                return None
            directory = Path(certificate).resolve().parent
            root = self.connection_record.parent.resolve()
            if (
                directory.parent != root
                or not directory.name.startswith("tls-daemon-")
                or directory.is_symlink()
            ):
                return None
            return directory
        except (OSError, json.JSONDecodeError):
            return None


def attach_runtime(runtime: Any, lifecycle: HypermidLifecycle) -> None:
    if getattr(runtime, "hypermid", None) not in (None, lifecycle):
        raise RuntimeError("RuntimeCoordinator already has a Hypermid lifecycle")
    runtime.hypermid = lifecycle


def _scoped_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def runtime_scope(runtime: Any) -> Scope:
    from gideon.core.config.loader import default_workspace_dir

    owner_source = str(getattr(runtime, "owner_id", "") or "")
    if not owner_source:
        owner_source = f"local-uid:{os.getuid()}" if hasattr(os, "getuid") else "local-user"
    project_source = str(
        os.environ.get("GIDEON_PROJECT_ID")
        or os.environ.get("GIDEON_PROJECT_DIR")
        or default_workspace_dir()
        or "gideon-default-project"
    )
    workspace_source = str(os.environ.get("GIDEON_WORKSPACE") or "").strip()
    return Scope(
        owner_id=_scoped_id("owner", owner_source),
        project_id=_scoped_id("project", project_source),
        workspace_id=(
            _scoped_id("workspace", workspace_source) if workspace_source else None
        ),
    )


def _daemon_executable() -> str:
    configured = str(
        os.environ.get("HYPERMID_DAEMON_BINARY")
        or os.environ.get("GIDEON_HYPERMID_DAEMON")
        or ""
    ).strip()
    if configured:
        return configured
    installed = shutil.which("hypermid-daemon")
    if installed:
        return installed
    packaged = Path(__file__).resolve().parent / "bin" / (
        "hypermid-daemon.exe" if os.name == "nt" else "hypermid-daemon"
    )
    if packaged.is_file() and os.access(packaged, os.X_OK):
        return str(packaged)
    target = str(os.environ.get("CARGO_TARGET_DIR") or "").strip()
    if target:
        candidate = Path(target) / "debug" / "hypermid-daemon"
        if candidate.is_file():
            return str(candidate)
    return "hypermid-daemon"


def build_runtime_lifecycle(runtime: Any) -> HypermidLifecycle:
    from gideon.core.config.loader import config_dir

    from .config import (
        DaemonConfig,
        DaemonTransport,
        LocalAuthConfig,
        LocalAuthMethod,
    )
    from .config_store import ContextConfigStore

    context_config = getattr(runtime.config, "hypermid", None)
    mode = getattr(context_config, "mode", "off")
    hypermid_dir = config_dir() / "hypermid"
    state_dir = hypermid_dir / "runtime"
    socket_path = state_dir / "hypermid.sock"
    record_path = state_dir / "connection.json"
    scope = runtime_scope(runtime)
    enrollment_path = hypermid_dir / "enrollment.json"
    enrollment = (
        LocalEnrollment.load(enrollment_path, scope=scope)
        if enrollment_path.is_file()
        else None
    )
    is_windows = os.name == "nt"
    daemon = DaemonConfig(
        transport=(
            DaemonTransport.LOOPBACK_TCP if is_windows else DaemonTransport.UNIX_SOCKET
        ),
        endpoint="127.0.0.1:0" if is_windows else str(socket_path),
        executable=_daemon_executable(),
        start_on_demand=True,
        auth=LocalAuthConfig(
            method=(
                LocalAuthMethod.HMAC_TOKEN_FILE
                if is_windows
                else LocalAuthMethod.PEER_AND_HMAC
            ),
            token_file=str(state_dir / "auth.token"),
            require_peer_identity=not is_windows,
        ),
    ).with_connection_record(str(record_path))
    lifecycle = HypermidLifecycle(
        HypermidAdapter(
            HypermidClient(record_path, scope=scope), mode=mode
        ),
        daemon,
        connection_record=record_path,
        module_supervisor=HypermidModuleSupervisor(
            state_dir / "modules", scope=scope
        ),
        enrollment=enrollment,
        config_store=ContextConfigStore(hypermid_dir / "configuration.sqlite3"),
        initial_config=context_config,
        runtime=runtime,
    )
    from .credential_authority import attach_security_operations

    attach_security_operations(lifecycle)
    attach_runtime(runtime, lifecycle)
    return lifecycle


async def start_runtime(runtime: Any) -> HypermidStatus:
    lifecycle = getattr(runtime, "hypermid", None) or build_runtime_lifecycle(runtime)
    return await lifecycle.start()


async def stop_runtime(runtime: Any) -> None:
    lifecycle = getattr(runtime, "hypermid", None)
    if lifecycle is not None:
        await lifecycle.stop()


__all__ = [
    "HypermidLifecycle",
    "LocalEnrollment",
    "attach_runtime",
    "build_runtime_lifecycle",
    "runtime_scope",
    "start_runtime",
    "stop_runtime",
]
