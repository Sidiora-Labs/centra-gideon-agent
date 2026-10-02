from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .models import Cursor, JsonValue, Scope, Trace

HookKind = Literal[
    "session_bind",
    "ingest",
    "project",
    "pressure_change",
    "reduction",
    "summary",
    "recovery",
    "subagent_snapshot",
    "diagnostic_fault",
]
HookPhase = Literal["pre", "post"]
HookOutcome = Literal["allow", "deny", "inject", "committed", "rejected", "timed_out", "failed"]
FailurePolicy = Literal["continue", "deny"]

MAX_METADATA = 64
MAX_SYNTHETIC_BLOCKS = 128
MAX_SYNTHETIC_TOKENS = 65_536
_REASON = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_KEYS = ("authorization", "credential", "password", "secret", "token", "content", "prompt")
_KINDS = {
    "session_bind",
    "ingest",
    "project",
    "pressure_change",
    "reduction",
    "summary",
    "recovery",
    "subagent_snapshot",
    "diagnostic_fault",
}


def _reason(value: str) -> str:
    if not isinstance(value, str) or _REASON.fullmatch(value) is None:
        raise ValueError("hook reason code is invalid")
    return value


def _redact_metadata(metadata: Mapping[str, object]) -> dict[str, bool | int | str]:
    if len(metadata) > MAX_METADATA:
        raise ValueError("hook metadata exceeds its bound")
    result: dict[str, bool | int | str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
            raise ValueError("hook metadata key is invalid")
        if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
            result[key] = "[redacted]"
        elif isinstance(value, bool):
            result[key] = value
        elif isinstance(value, int):
            result[key] = value
        elif isinstance(value, str):
            cleaned = "".join(character for character in value if character == " " or character.isprintable())
            lowered = cleaned.lower()
            result[key] = (
                "[redacted]"
                if "bearer " in lowered or "authorization:" in lowered or "token=" in lowered
                else cleaned[:512]
            )
        else:
            raise ValueError("hook metadata values must be scalar")
    return result


@dataclass(frozen=True, slots=True)
class HookEvent:
    event_id: str
    kind: HookKind
    scope: Scope
    trace: Trace
    session_id: str
    cursor: Cursor
    policy_revision: int
    phase: HookPhase
    metadata: Mapping[str, object]
    created_at: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS or self.phase not in ("pre", "post"):
            raise ValueError("hook kind or phase is invalid")
        if isinstance(self.policy_revision, bool) or not isinstance(self.policy_revision, int) or self.policy_revision < 1:
            raise ValueError("hook policy revision is invalid")
        if not self.created_at or len(self.created_at) > 64:
            raise ValueError("hook timestamp is invalid")
        object.__setattr__(self, "metadata", _redact_metadata(self.metadata))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "scope": self.scope.to_wire(),
            "trace": self.trace.to_wire(),
            "session_id": self.session_id,
            "cursor": self.cursor.to_wire(),
            "policy_revision": self.policy_revision,
            "phase": self.phase,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SyntheticBlockRef:
    block_id: str
    content_digest: str
    token_mass: int

    def __post_init__(self) -> None:
        if not isinstance(self.content_digest, str) or _DIGEST.fullmatch(self.content_digest) is None:
            raise ValueError("synthetic block digest is invalid")
        if isinstance(self.token_mass, bool) or not isinstance(self.token_mass, int) or not 0 <= self.token_mass <= MAX_SYNTHETIC_TOKENS:
            raise ValueError("synthetic block token mass is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "block_id": self.block_id,
            "content_digest": self.content_digest,
            "token_mass": self.token_mass,
        }


@dataclass(frozen=True, slots=True)
class HookDecision:
    outcome: Literal["allow", "deny", "inject"]
    reason_code: str | None = None
    synthetic_blocks: tuple[SyntheticBlockRef, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in ("allow", "deny", "inject"):
            raise ValueError("pre-hook decision is invalid")
        if self.reason_code is not None:
            _reason(self.reason_code)
        if self.outcome == "deny" and self.reason_code is None:
            raise ValueError("denial requires a reason code")
        if self.outcome == "inject":
            if not self.synthetic_blocks or len(self.synthetic_blocks) > MAX_SYNTHETIC_BLOCKS:
                raise ValueError("injection requires bounded synthetic blocks")
            if sum(block.token_mass for block in self.synthetic_blocks) > MAX_SYNTHETIC_TOKENS:
                raise ValueError("synthetic block token limit exceeded")
        elif self.synthetic_blocks:
            raise ValueError("only injection may contain synthetic blocks")

    @classmethod
    def allow(cls) -> HookDecision:
        return cls("allow")

    @classmethod
    def deny(cls, reason_code: str) -> HookDecision:
        return cls("deny", reason_code)

    @classmethod
    def inject(cls, blocks: Sequence[SyntheticBlockRef]) -> HookDecision:
        return cls("inject", synthetic_blocks=tuple(blocks))


HookCallable = Callable[[HookEvent], HookDecision | Awaitable[HookDecision]]


@dataclass(frozen=True, slots=True)
class HookRegistration:
    hook_id: str
    kinds: frozenset[HookKind]
    phases: frozenset[HookPhase]
    timeout_ms: int
    callback: HookCallable = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if isinstance(self.timeout_ms, bool) or not isinstance(self.timeout_ms, int) or not 1 <= self.timeout_ms <= 60_000:
            raise ValueError("hook timeout must be between 1 and 60000 milliseconds")


@dataclass(frozen=True, slots=True)
class HookOutcomeRecord:
    sequence: int
    event_id: str
    hook_id: str
    kind: HookKind
    phase: HookPhase
    trace: Trace
    outcome: HookOutcome
    reason_code: str | None
    duration_ms: int
    synthetic_blocks: tuple[SyntheticBlockRef, ...]
    recorded_at_ms: int

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "hook_id": self.hook_id,
            "kind": self.kind,
            "phase": self.phase,
            "trace": self.trace.to_wire(),
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "synthetic_blocks": [block.to_wire() for block in self.synthetic_blocks],
            "recorded_at_ms": self.recorded_at_ms,
        }
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        return result


@dataclass(frozen=True, slots=True)
class HookReplay:
    gap: bool
    oldest_cursor: int
    next_cursor: int
    outcomes: tuple[HookOutcomeRecord, ...]


class HookOutcomeStore:
    def __init__(self, *, retention: int = 256) -> None:
        if isinstance(retention, bool) or not isinstance(retention, int) or not 1 <= retention <= 4096:
            raise ValueError("hook retention must be between 1 and 4096")
        self._lock = asyncio.Lock()
        self._outcomes: deque[HookOutcomeRecord] = deque(maxlen=retention)
        self._next_sequence = 1

    async def append(self, record: HookOutcomeRecord) -> HookOutcomeRecord:
        async with self._lock:
            stored = HookOutcomeRecord(
                sequence=self._next_sequence,
                event_id=record.event_id,
                hook_id=record.hook_id,
                kind=record.kind,
                phase=record.phase,
                trace=record.trace,
                outcome=record.outcome,
                reason_code=record.reason_code,
                duration_ms=record.duration_ms,
                synthetic_blocks=record.synthetic_blocks,
                recorded_at_ms=record.recorded_at_ms,
            )
            self._next_sequence += 1
            self._outcomes.append(stored)
            return stored

    async def read_after(self, cursor: int) -> HookReplay:
        async with self._lock:
            oldest = self._outcomes[0].sequence if self._outcomes else self._next_sequence
            latest = self._next_sequence - 1
            return HookReplay(
                gap=cursor + 1 < oldest,
                oldest_cursor=max(0, oldest - 1),
                next_cursor=latest,
                outcomes=tuple(record for record in self._outcomes if record.sequence > cursor),
            )


@dataclass(frozen=True, slots=True)
class HookDispatchResult:
    allowed: bool
    reason_code: str | None
    synthetic_blocks: tuple[SyntheticBlockRef, ...]
    outcomes: tuple[HookOutcomeRecord, ...]


class HookDispatcher:
    def __init__(self, store: HookOutcomeStore, *, failure_policy: FailurePolicy = "deny") -> None:
        if failure_policy not in ("continue", "deny"):
            raise ValueError("hook failure policy is invalid")
        self._store = store
        self._failure_policy = failure_policy
        self._hooks: list[HookRegistration] = []

    def register(self, registration: HookRegistration) -> None:
        self._hooks.append(registration)
        self._hooks.sort(key=lambda hook: hook.hook_id)

    async def dispatch_pre(self, event: HookEvent, *, recorded_at_ms: int) -> HookDispatchResult:
        if event.phase != "pre":
            raise ValueError("dispatch_pre requires a pre event")
        return await self._dispatch(event, recorded_at_ms=recorded_at_ms, enforcing=True)

    async def dispatch_post(self, event: HookEvent, *, recorded_at_ms: int) -> HookDispatchResult:
        if event.phase != "post":
            raise ValueError("dispatch_post requires a post event")
        return await self._dispatch(event, recorded_at_ms=recorded_at_ms, enforcing=False)

    async def _dispatch(self, event: HookEvent, *, recorded_at_ms: int, enforcing: bool) -> HookDispatchResult:
        blocks: list[SyntheticBlockRef] = []
        outcomes: list[HookOutcomeRecord] = []
        allowed = True
        denial_reason: str | None = None
        for hook in self._hooks:
            if event.kind not in hook.kinds or event.phase not in hook.phases:
                continue
            started = time.monotonic()
            outcome: HookOutcome
            reason_code: str | None = None
            hook_blocks: tuple[SyntheticBlockRef, ...] = ()
            try:
                pending = hook.callback(event)
                if inspect.isawaitable(pending):
                    decision = await asyncio.wait_for(pending, hook.timeout_ms / 1000)
                else:
                    decision = pending
                if not isinstance(decision, HookDecision):
                    raise TypeError("hook returned an invalid decision")
                outcome = decision.outcome
                reason_code = decision.reason_code
                hook_blocks = decision.synthetic_blocks
            except asyncio.TimeoutError:
                outcome = "timed_out"
                reason_code = "hook_timed_out"
            except Exception:
                outcome = "failed"
                reason_code = "hook_failed"
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            stored = await self._store.append(
                HookOutcomeRecord(
                    sequence=0,
                    event_id=event.event_id,
                    hook_id=hook.hook_id,
                    kind=event.kind,
                    phase=event.phase,
                    trace=event.trace,
                    outcome=outcome,
                    reason_code=reason_code,
                    duration_ms=duration_ms,
                    synthetic_blocks=hook_blocks,
                    recorded_at_ms=recorded_at_ms,
                )
            )
            outcomes.append(stored)
            if not enforcing:
                continue
            if outcome == "inject":
                candidate = [*blocks, *hook_blocks]
                if len(candidate) > MAX_SYNTHETIC_BLOCKS or sum(block.token_mass for block in candidate) > MAX_SYNTHETIC_TOKENS:
                    allowed = False
                    denial_reason = "synthetic_block_limit"
                    blocks.clear()
                    break
                blocks = candidate
            elif outcome == "deny" or (outcome in ("timed_out", "failed") and self._failure_policy == "deny"):
                allowed = False
                denial_reason = reason_code
                blocks.clear()
                break
        return HookDispatchResult(allowed, denial_reason, tuple(blocks), tuple(outcomes))
