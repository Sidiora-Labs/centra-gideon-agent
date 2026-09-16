"""Effect event projections, standing commitments and bounded teardown execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shlex
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows import store

logger = logging.getLogger(__name__)


class EffectStatus(str, Enum):
    """One effect's lifecycle. `ATTEMPTED` is written BEFORE dispatch — a crash between"""

    ATTEMPTED = "attempted"
    COMMITTED = "committed"
    RETRIED = "retried"
    COMPENSATED = "compensated"
    SKIPPED = "skipped"


def idempotency_key(run_id: str, instance_path: str, epoch: int) -> str:
    """The effect's identity: sha256(run_id + instance_path + epoch)."""
    raw = f"{run_id}|{instance_path}|{epoch}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class EffectRecord:
    """One journaled effect event, read back from `events.jsonl`."""

    instance_path: str = ""
    idempotency_key: str = ""
    effect_status: EffectStatus = EffectStatus.ATTEMPTED
    epoch: int = 0
    node_id: str = ""
    provider: str = ""
    output_id: str = ""
    compensation_ref: str = ""

    @classmethod
    def from_event(cls, rec: dict[str, Any]) -> EffectRecord:
        raw = str(rec.get("effect_status", "attempted") or "attempted")
        status = next(
            (state for state in EffectStatus if state.value == raw),
            EffectStatus.ATTEMPTED,
        )
        values: dict[str, Any] = {}
        for name in (
            "instance_path",
            "idempotency_key",
            "effect_status",
            "epoch",
            "node_id",
            "provider",
            "output_id",
            "compensation_ref",
        ):
            if name == "effect_status":
                values[name] = status
            elif name == "epoch":
                values[name] = int(rec.get(name, 0) or 0)
            else:
                values[name] = str(rec.get(name, "") or "")
        return cls(**values)


def effect_history(run_id: str) -> dict[str, list[EffectRecord]]:
    from gideon.automation.workflows.journal import EFFECT, EVENTS_FILE

    records = (
        EffectRecord.from_event(row)
        for row in store.read_jsonl(run_id, EVENTS_FILE)
        if row.get("kind") == EFFECT
    )
    grouped: dict[str, list[EffectRecord]] = {}
    for record in records:
        grouped.setdefault(record.instance_path, []).append(record)
    return grouped


def committed_effect(records: list[EffectRecord]) -> EffectRecord | None:
    standing = None
    for event in records:
        if event.effect_status == EffectStatus.COMMITTED:
            standing = event
        elif (
            event.effect_status == EffectStatus.COMPENSATED
            and standing is not None
            and event.idempotency_key == standing.idempotency_key
        ):
            standing = None
    return standing


def redo_blocked(
    node_config: dict[str, Any], committed: EffectRecord | None, epoch: int
) -> bool:
    """True when re-execution must be refused (WF2-R1)."""
    if committed is None or committed.epoch == epoch:
        return False
    return not bool((node_config or {}).get("redo_effects", False))


def parse_byoi_output(stdout: str) -> dict[str, Any] | None:
    try:
        result = json.loads((stdout or "").strip())
    except ValueError:
        return None
    return result if isinstance(result, dict) else None


def output_id_of(output: Any) -> str:
    """The external resource id a teardown receives, from the provider's JSON output."""
    if isinstance(output, dict):
        return str(output.get("id", "") or "")
    return ""


async def run_teardown(
    command: str, output_id: str, *, runner: Any = None, timeout: float = 60.0
) -> tuple[bool, str]:
    if runner is not None:
        try:
            return await runner(command, output_id)
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:500]
    invocation, error = _TeardownInvocation.prepare(command, output_id)
    return (False, error) if invocation is None else await invocation.run(timeout)


@dataclass
class CallerDedupe:
    """Short-lived caller-key → run-id cache (WF2-R1)."""

    ttl_secs: float = 900.0
    clock: Any = time.monotonic
    _entries: dict[str, tuple[str, float]] = field(default_factory=dict)

    def remember(self, caller_key: str, run_id: str) -> None:
        if caller_key:
            self._sweep()
            stamp = float(self.clock())
            self._entries.update({caller_key: (run_id, stamp)})

    def lookup(self, caller_key: str) -> str | None:
        if caller_key:
            entry = self._entries.get(caller_key)
            if entry is not None:
                expired = float(self.clock()) - entry[1] > self.ttl_secs
                if not expired:
                    return entry[0]
                self._entries.pop(caller_key, None)
        return None

    def _sweep(self) -> None:
        observed = float(self.clock())
        expired = tuple(
            key
            for key, value in self._entries.items()
            if observed - value[1] > self.ttl_secs
        )
        for key in expired:
            self._entries.pop(key, None)


START_DEDUPE = CallerDedupe()


@dataclass(frozen=True)
class _TeardownInvocation:
    argv: list[str]
    environment: dict[str, str]

    @classmethod
    def prepare(
        cls, command: str, output_id: str
    ) -> tuple[_TeardownInvocation | None, str]:
        import os
        import shutil

        arguments = shlex.split(command)
        if not arguments:
            return None, "empty teardown command"
        executable = arguments[0]
        located = (
            os.path.exists(executable)
            if os.path.sep in executable
            else bool(shutil.which(executable))
        )
        if not located:
            return None, f"teardown command not found: {executable}"
        arguments.append(output_id)
        environment = dict(os.environ)
        environment["EFFECT_OUTPUT_ID"] = output_id
        return cls(arguments, environment), ""

    async def run(self, timeout: float) -> tuple[bool, str]:
        from gideon.security.sandbox import PROFILE_TOOL, create_subprocess_limited

        try:
            process = await create_subprocess_limited(
                *self.argv,
                profile=PROFILE_TOOL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.environment,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return False, f"teardown timed out after {timeout}s"
        except FileNotFoundError:
            return False, f"teardown command not found: {self.argv[0]}"
        except OSError as exc:
            return False, f"teardown could not start: {exc}"[:500]
        success = process.returncode == 0
        detail = ((stdout if success else stderr) or b"").decode("utf-8", "replace")[
            :2000
        ]
        return success, (
            detail if success else f"teardown exited {process.returncode}: {detail}"
        )
