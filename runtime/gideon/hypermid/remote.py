"""Remote access control and sandboxed remote-module execution."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, TypedDict, cast

from gideon.integrations.sandbox_providers.base import SandboxSpec
from gideon.integrations.sandbox_providers.registry import resolve_provider
from gideon.security.net.guard import evaluate
from gideon.security.net.policy import STRICT, EgressPolicy
from gideon.security.security import redact_and_truncate

from .federation import EffectClass, FederatedCall, FederationSession
from .foundation import Digest, EffectState, Id, Scope

_MAX_REMOTE_FRAME = 8 * 1024 * 1024
_MAX_REMOTE_DIAGNOSTIC = 64 * 1024
_RESERVED_AUTHORITIES = frozenset(
    {"approval", "approvals", "tool_call", "tool_calls", "final", "final_delivery"}
)


class RemoteViolation(RuntimeError):
    pass


class RemoteOutcomeUnknown(RemoteViolation):
    pass


@dataclass(frozen=True, slots=True)
class DeviceEnrollment:
    device_id: Id
    name: str
    expires_at: int
    capabilities: tuple[str, ...]
    state: str = "active"

    def to_wire(self) -> dict[str, object]:
        return {
            "device_id": str(self.device_id),
            "name": self.name,
            "expires_at": self.expires_at,
            "capabilities": list(self.capabilities),
            "state": self.state,
        }


class _DeviceState(TypedDict):
    device_id: Id
    name: str
    expires_at: int
    capabilities: tuple[str, ...]
    state: str


class RemoteAccessService:
    """Reviewed, durable local configuration for opt-in remote listening."""

    def __init__(self, path: str | Path, scope: Scope) -> None:
        self.path = Path(path)
        self.scope = scope
        self._pending: dict[Digest, dict[str, object]] = {}
        self._state = self._load()

    def status(self) -> dict[str, object]:
        enabled = bool(self._state.get("enabled", False))
        devices = [
            DeviceEnrollment(**item).to_wire()
            for item in cast(list[_DeviceState], self._state.get("devices", []))
        ]
        state = "remote" if enabled else "local"
        if (
            enabled
            and devices
            and all(device["state"] == "revoked" for device in devices)
        ):
            state = "revoked"
        tls: dict[str, object] = {"configured": enabled}
        if enabled:
            tls.update(
                endpoint=self._state["endpoint"],
                server_name=self._state["server_name"],
            )
        return {
            "state": state,
            "enabled": enabled,
            "tls": tls,
            "devices": devices,
        }

    def plan_enable(
        self,
        *,
        endpoint: str,
        server_name: str,
        device_name: str,
        expires_at: int,
        capabilities: list[str] | tuple[str, ...],
    ) -> dict[str, object]:
        if (
            not endpoint.startswith("tcp://")
            or not server_name
            or ":" not in endpoint[6:]
        ):
            raise RemoteViolation(
                "remote endpoint must be an explicit TCP address protected by TLS"
            )
        if not device_name or len(device_name) > 128:
            raise RemoteViolation("remote device name is invalid")
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= int(time.time() * 1000)
        ):
            raise RemoteViolation("remote enrollment expiry must be in the future")
        if not isinstance(capabilities, (list, tuple)) or any(
            not isinstance(item, str) for item in capabilities
        ):
            raise RemoteViolation("remote capabilities must be an array of strings")
        normalized = tuple(sorted(set(capabilities)))
        if not normalized or any(
            not item
            or len(item) > 160
            or item.startswith("writer.")
            or item in _RESERVED_AUTHORITIES
            for item in normalized
        ):
            raise RemoteViolation(
                "remote capabilities contain an invalid or Gideon-owned authority"
            )
        plan: dict[str, object] = {
            "endpoint": endpoint,
            "server_name": server_name,
            "device_name": device_name,
            "expires_at": expires_at,
            "capabilities": list(normalized),
            "scope_label": f"{self.scope.owner_id}/{self.scope.project_id}",
            "warnings": [
                "Remote access remains limited to the exact owner and project scope.",
                "Remote peers cannot acquire the local writer lease or Gideon approvals.",
            ],
        }
        digest = Digest.sha256(_canonical(plan))
        self._pending[digest] = dict(plan)
        return {"plan_digest": str(digest), **plan}

    def enable(self, plan_digest: Digest) -> dict[str, object]:
        try:
            plan = self._pending.pop(Digest(plan_digest))
        except KeyError as exc:
            raise RemoteViolation(
                "remote enable requires the exact reviewed plan digest"
            ) from exc
        device = {
            "device_id": Id(f"device-{str(plan_digest)[:24]}"),
            "name": plan["device_name"],
            "expires_at": plan["expires_at"],
            "capabilities": tuple(cast(list[str], plan["capabilities"])),
            "state": "active",
        }
        self._state = {
            "enabled": True,
            "endpoint": plan["endpoint"],
            "server_name": plan["server_name"],
            "scope": self.scope.to_wire(),
            "devices": [device],
        }
        self._save()
        return self.status()

    def revoke(self, device_id: Id) -> dict[str, object]:
        found = False
        devices: list[dict[str, object]] = []
        for device in cast(list[dict[str, object]], self._state.get("devices", [])):
            if device["device_id"] == device_id:
                device = {**device, "state": "revoked"}
                found = True
            devices.append(device)
        if not found:
            raise RemoteViolation("remote device is not enrolled")
        self._state["devices"] = devices
        self._save()
        return self.status()

    def invoke(
        self, operation: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        if operation == "remote.status":
            return self.status()
        if operation == "remote.enable.plan":
            return self.plan_enable(
                endpoint=str(payload.get("endpoint", "")),
                server_name=str(payload.get("server_name", "")),
                device_name=str(payload.get("device_name", "")),
                expires_at=cast(int, payload.get("expires_at", 0)),
                capabilities=cast(list[str], payload.get("capabilities", [])),
            )
        if operation == "remote.enable":
            return self.enable(Digest(payload.get("plan_digest", "")))
        if operation == "remote.devices.revoke":
            return self.revoke(Id(payload.get("device_id", "")))
        raise RemoteViolation("unknown remote access operation")

    def _load(self) -> dict[str, object]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled": False, "devices": []}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RemoteViolation("remote access state is unreadable") from exc
        if not isinstance(raw, dict) or raw.get("scope") != self.scope.to_wire():
            raise RemoteViolation("remote access state belongs to another scope")
        return raw

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_canonical(self._state))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


@dataclass(frozen=True, slots=True)
class RemoteEffectRecord:
    effect_id: Id
    input_digest: Digest
    operation: str
    scope: Scope
    state: str
    result_digest: Digest | None = None
    result: object = None
    reason: str | None = None

    def to_wire(self) -> dict[str, object]:
        value: dict[str, object] = {
            "effect_id": str(self.effect_id),
            "input_digest": str(self.input_digest),
            "operation": self.operation,
            "scope": self.scope.to_wire(),
            "state": self.state,
        }
        if self.result_digest is not None:
            value["result_digest"] = str(self.result_digest)
        if self.result is not None:
            value["result"] = self.result
        if self.reason is not None:
            value["reason"] = self.reason
        return value


class RemoteEffectLedger:
    """Append-only mutation state; dispatched recovery is always unknown."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: dict[Id, RemoteEffectRecord] = {}
        self._load()
        for record in tuple(self._records.values()):
            if record.state == "intent":
                self.settle(
                    record.effect_id,
                    EffectState.NOT_STARTED,
                    reason="restarted before dispatch",
                )
            elif record.state == "dispatched":
                self.settle(
                    record.effect_id,
                    EffectState.UNKNOWN,
                    reason="restarted after dispatch",
                )

    def begin(self, call: FederatedCall) -> RemoteEffectRecord:
        if call.effect_id is None or call.input_digest is None:
            raise RemoteViolation("durable call is missing its effect identity")
        current = self._records.get(call.effect_id)
        if current is not None:
            if (
                current.input_digest != call.input_digest
                or current.operation != call.operation
                or current.scope != call.scope
            ):
                raise RemoteViolation("effect id was reused with divergent content")
            return current
        record = RemoteEffectRecord(
            call.effect_id, call.input_digest, call.operation, call.scope, "intent"
        )
        self._append(record)
        return record

    def dispatched(self, effect_id: Id) -> RemoteEffectRecord:
        current = self._require(effect_id)
        if current.state != "intent":
            raise RemoteViolation("effect cannot be dispatched from its current state")
        return self._replace(replace(current, state="dispatched"))

    def settle(
        self,
        effect_id: Id,
        state: EffectState,
        *,
        result: object = None,
        reason: str | None = None,
    ) -> RemoteEffectRecord:
        current = self._require(effect_id)
        if current.state not in ("intent", "dispatched", "unknown"):
            if current.state == state.value:
                return current
            raise RemoteViolation("effect is already settled")
        digest = (
            Digest.sha256(_canonical(result))
            if state is EffectState.COMMITTED
            else None
        )
        return self._replace(
            replace(
                current,
                state=state.value,
                result=result,
                result_digest=digest,
                reason=reason,
            )
        )

    def status(self, effect_id: Id) -> RemoteEffectRecord | None:
        return self._records.get(effect_id)

    def _require(self, effect_id: Id) -> RemoteEffectRecord:
        try:
            return self._records[effect_id]
        except KeyError as exc:
            raise RemoteViolation("unknown remote effect") from exc

    def _replace(self, record: RemoteEffectRecord) -> RemoteEffectRecord:
        self._append(record)
        return record

    def _append(self, record: RemoteEffectRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.path, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(_canonical(record.to_wire()) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._records[record.effect_id] = record

    def _load(self) -> None:
        try:
            lines = self.path.read_bytes().splitlines()
        except FileNotFoundError:
            return
        for line in lines:
            try:
                raw = json.loads(line)
                record = RemoteEffectRecord(
                    effect_id=Id(raw["effect_id"]),
                    input_digest=Digest(raw["input_digest"]),
                    operation=raw["operation"],
                    scope=Scope.from_wire(raw["scope"]),
                    state=raw["state"],
                    result_digest=(
                        Digest(raw["result_digest"]) if "result_digest" in raw else None
                    ),
                    result=raw.get("result"),
                    reason=raw.get("reason"),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RemoteViolation("remote effect journal is corrupt") from exc
            self._records[record.effect_id] = record


@dataclass(frozen=True, slots=True)
class RemoteModuleLaunch:
    argv: tuple[str, ...]
    working_directory: Path
    executable_path: Path
    executable_digest: Digest
    sandbox_provider: str = "docker"
    egress_url: str | None = None
    egress_policy: EgressPolicy = STRICT
    egress_tier: str = "off"
    environment: tuple[tuple[str, str], ...] = ()


class RemoteModuleExecutor:
    def __init__(self, ledger: RemoteEffectLedger) -> None:
        self.ledger = ledger

    async def execute(
        self,
        session: FederationSession,
        call: FederatedCall,
        launch: RemoteModuleLaunch,
        *,
        cancellation: asyncio.Event | None = None,
    ) -> RemoteEffectRecord:
        session.admit(call)
        if launch.sandbox_provider in ("", "none"):
            raise RemoteViolation(
                "remote modules require a sandbox provider with enforced network isolation"
            )
        if launch.egress_url is not None:
            decision = evaluate(launch.egress_url, launch.egress_policy)
            if not decision.allow:
                raise RemoteViolation(f"remote egress denied: {decision.reason}")
            raise RemoteViolation(
                "remote module network remains disabled until its sandbox provider "
                "enforces the approved destination"
            )
        if launch.egress_tier != "off":
            raise RemoteViolation(
                "remote module egress requires a destination-specific enforced grant"
            )
        if not launch.argv or not launch.working_directory.is_dir():
            raise RemoteViolation("remote module launch is incomplete")
        try:
            executable = launch.executable_path.resolve(strict=True)
            working_directory = launch.working_directory.resolve(strict=True)
            executable.relative_to(working_directory)
        except (OSError, ValueError) as exc:
            raise RemoteViolation(
                "remote module executable escapes its working directory"
            ) from exc
        if (
            not executable.is_file()
            or Digest.sha256(executable.read_bytes()) != launch.executable_digest
        ):
            raise RemoteViolation("remote module executable digest does not match")
        if os.fspath(launch.executable_path) not in launch.argv:
            raise RemoteViolation(
                "verified remote module executable is absent from argv"
            )
        for name, _value in launch.environment:
            upper = name.upper()
            if any(
                token in upper
                for token in (
                    "AUTH",
                    "CREDENTIAL",
                    "KEY",
                    "PASSWORD",
                    "SECRET",
                    "TOKEN",
                )
            ):
                raise RemoteViolation(
                    "remote module environment cannot carry credentials"
                )
        durable = call.effect is EffectClass.DURABLE
        if durable:
            current = self.ledger.begin(call)
            if current.state == EffectState.COMMITTED.value:
                return current
            if current.state == EffectState.UNKNOWN.value:
                raise RemoteOutcomeUnknown(
                    current.reason or "remote effect outcome is unknown"
                )
            if current.state == EffectState.NOT_STARTED.value:
                return current
        if cancellation is not None and cancellation.is_set():
            if durable and call.effect_id is not None:
                return self.ledger.settle(
                    call.effect_id,
                    EffectState.NOT_STARTED,
                    reason="cancelled before dispatch",
                )
            raise asyncio.CancelledError
        handle = None
        process: asyncio.subprocess.Process | None = None
        read_task: asyncio.Task[bytes] | None = None
        cancel_task: asyncio.Task[bool] | None = None
        dispatched = False
        try:
            provider = resolve_provider(launch.sandbox_provider)
            spec = SandboxSpec(
                mode="strict",
                profile="tool",
                workspace_dir=os.fspath(launch.working_directory),
                egress_tier=launch.egress_tier,
                env=dict(launch.environment),
                safety_profile="remote_module",
            )
            handle = provider.wrap(spec, list(launch.argv))
            process = await handle.exec(
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=launch.working_directory,
                env=dict(launch.environment),
                start_new_session=True,
                limit=_MAX_REMOTE_FRAME + 1,
            )
            if durable and call.effect_id is not None:
                self.ledger.dispatched(call.effect_id)
            dispatched = True
            assert process.stdin is not None and process.stdout is not None
            request = _canonical(call.to_wire())
            if len(request) > _MAX_REMOTE_FRAME:
                raise RemoteViolation("remote request exceeds the frame bound")
            process.stdin.write(request + b"\n")
            await process.stdin.drain()
            process.stdin.close()
            read_task = asyncio.create_task(process.stdout.readline())
            cancel_task = (
                asyncio.create_task(cancellation.wait())
                if cancellation is not None
                else None
            )
            deadline = max(0.0, (call.deadline_ms - int(time.time() * 1000)) / 1000)
            waiters: set[asyncio.Task[bytes] | asyncio.Task[bool]] = {read_task}
            if cancel_task is not None:
                waiters.add(cancel_task)
            done, _ = await asyncio.wait(
                waiters, timeout=deadline, return_when=asyncio.FIRST_COMPLETED
            )
            if read_task not in done:
                await _terminate(process)
                reason = (
                    "cancelled after dispatch"
                    if cancel_task in done
                    else "deadline elapsed after dispatch"
                )
                if durable and call.effect_id is not None:
                    self.ledger.settle(
                        call.effect_id, EffectState.UNKNOWN, reason=reason
                    )
                    raise RemoteOutcomeUnknown(reason)
                if cancel_task in done:
                    raise asyncio.CancelledError
                raise TimeoutError(reason)
            try:
                response_bytes = read_task.result()
            except ValueError as exc:
                reason = await _invalid_frame_reason(process, "oversized")
                raise RemoteViolation(reason) from exc
            if not response_bytes or len(response_bytes) > _MAX_REMOTE_FRAME:
                kind = "empty" if not response_bytes else "oversized"
                raise RemoteViolation(await _invalid_frame_reason(process, kind))
            wait_task = asyncio.create_task(process.wait())
            remaining = max(0.0, (call.deadline_ms - int(time.time() * 1000)) / 1000)
            completion_waiters: set[asyncio.Task[object]] = {wait_task}
            if cancel_task is not None:
                completion_waiters.add(cancel_task)
            completed, _ = await asyncio.wait(
                completion_waiters,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wait_task not in completed:
                await _terminate(process)
                reason = (
                    "cancelled after dispatch"
                    if cancel_task in completed
                    else "deadline elapsed after dispatch"
                )
                if durable and call.effect_id is not None:
                    self.ledger.settle(
                        call.effect_id, EffectState.UNKNOWN, reason=reason
                    )
                    raise RemoteOutcomeUnknown(reason)
                if cancel_task in completed:
                    raise asyncio.CancelledError
                raise TimeoutError(reason)
            if process.returncode != 0:
                raise RemoteViolation(
                    "remote module exited without a committed response"
                )
            if await process.stdout.read(1):
                raise RemoteViolation("remote module returned unsolicited extra frames")
            try:
                response = json.loads(response_bytes)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise RemoteViolation("remote module returned malformed JSON") from exc
            if not isinstance(response, dict) or _RESERVED_AUTHORITIES.intersection(
                response
            ):
                raise RemoteViolation(
                    "remote module attempted to return Gideon-owned authority"
                )
            if set(response) != {"result"}:
                raise RemoteViolation("remote module response fields are invalid")
            if durable and call.effect_id is not None:
                return self.ledger.settle(
                    call.effect_id, EffectState.COMMITTED, result=response["result"]
                )
            return RemoteEffectRecord(
                effect_id=Id(f"query-{call.message_id}"),
                input_digest=Digest.sha256(_canonical(call.payload)),
                operation=call.operation,
                scope=call.scope,
                state=EffectState.COMMITTED.value,
                result_digest=Digest.sha256(_canonical(response["result"])),
                result=response["result"],
            )
        except (RemoteOutcomeUnknown, asyncio.CancelledError, TimeoutError):
            raise
        except Exception as exc:
            if process is not None and process.returncode is None:
                await _terminate(process)
            if durable and call.effect_id is not None:
                state = EffectState.UNKNOWN if dispatched else EffectState.NOT_STARTED
                record = self.ledger.settle(call.effect_id, state, reason=str(exc))
                if state is EffectState.UNKNOWN:
                    raise RemoteOutcomeUnknown(
                        record.reason or "remote effect outcome is unknown"
                    ) from exc
                return record
            raise
        finally:
            for task in (read_task, cancel_task):
                if task is not None and not task.done():
                    task.cancel()
            if handle is not None:
                handle.cleanup()


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=0.5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def _invalid_frame_reason(process: asyncio.subprocess.Process, kind: str) -> str:
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except TimeoutError:
            await _terminate(process)
    stderr = b""
    if process.stderr is not None:
        try:
            stderr = await asyncio.wait_for(
                process.stderr.read(_MAX_REMOTE_DIAGNOSTIC), timeout=0.5
            )
        except TimeoutError:
            stderr = b""
    diagnostic = redact_and_truncate(
        stderr.decode("utf-8", errors="replace"), max_chars=4096
    ).strip()
    detail = diagnostic or "empty"
    return (
        f"remote module returned an invalid {kind} frame "
        f"(returncode={process.returncode}; stderr={detail})"
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "DeviceEnrollment",
    "RemoteAccessService",
    "RemoteEffectLedger",
    "RemoteEffectRecord",
    "RemoteModuleExecutor",
    "RemoteModuleLaunch",
    "RemoteOutcomeUnknown",
    "RemoteViolation",
]
