from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .foundation import ContractViolation, Error, Id, Scope, Trace

ROLE_VERSION = "hypermid.transform/v1"
OPERATIONS = ("declare", "describe", "hook")
MAX_BUDGET_MS = 60_000
MAX_QUESTION_BYTES = 2_048


class TransformViolation(ContractViolation):
    pass


class Hook(str, Enum):
    PRE_USER = "pre_user"
    POST_ASSISTANT = "post_assistant"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


class Phase(str, Enum):
    MUTATE = "mutate"
    VALIDATE = "validate"
    APPROVE = "approve"


class TextOperation(str, Enum):
    PREPEND = "prepend"
    APPEND = "append"
    REPLACE = "replace"


class UnavailablePolicy(str, Enum):
    CONTINUE = "continue"
    FAIL_STEP = "fail_step"
    FAIL_RUN = "fail_run"


@dataclass(frozen=True, slots=True)
class TransformSubscription:
    hook: Hook
    phase: Phase | None
    tools: frozenset[str]
    ops: frozenset[TextOperation]
    on_unavailable: UnavailablePolicy
    budget_ms: int

    def __post_init__(self) -> None:
        if not 1 <= self.budget_ms <= MAX_BUDGET_MS or not self.ops:
            raise TransformViolation(
                "subscription budget and operations must be bounded"
            )
        if self.hook is Hook.PRE_TOOL:
            if self.phase is None:
                raise TransformViolation("pre-tool subscription requires a phase")
        elif self.phase is not None or self.tools:
            raise TransformViolation(
                "phase and tool filter are only legal for pre-tool"
            )

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> TransformSubscription:
        _keys(value, {"hook", "ops", "on_unavailable", "budget_ms"}, {"phase", "tools"})
        tools = value.get("tools", [])
        ops = value["ops"]
        if not isinstance(tools, list) or not all(
            isinstance(tool, str) and 1 <= len(tool) <= 160 for tool in tools
        ):
            raise TransformViolation("tool filter is invalid")
        if not isinstance(ops, list):
            raise TransformViolation("operations must be an array")
        return cls(
            hook=Hook(value["hook"]),
            phase=Phase(value["phase"]) if value.get("phase") is not None else None,
            tools=frozenset(tools),
            ops=frozenset(TextOperation(operation) for operation in ops),
            on_unavailable=UnavailablePolicy(value["on_unavailable"]),
            budget_ms=_integer(value["budget_ms"], "budget_ms", 1, MAX_BUDGET_MS),
        )

    def to_wire(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "hook": self.hook.value,
            "tools": sorted(self.tools),
            "ops": sorted(operation.value for operation in self.ops),
            "on_unavailable": self.on_unavailable.value,
            "budget_ms": self.budget_ms,
        }
        if self.phase is not None:
            value["phase"] = self.phase.value
        return value


@dataclass(frozen=True, slots=True)
class HookRequest:
    call_id: Id
    declaration_id: Id
    scope: Scope
    hook: Hook
    phase: Phase | None
    tool_name: str | None
    subject: str
    now_ms: int
    deadline_ms: int
    trace: Trace

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> HookRequest:
        _keys(
            value,
            {
                "call_id",
                "declaration_id",
                "scope",
                "hook",
                "subject",
                "now_ms",
                "deadline_ms",
                "trace",
            },
            {"phase", "tool_name"},
        )
        hook = Hook(value["hook"])
        phase = Phase(value["phase"]) if value.get("phase") is not None else None
        tool_name = value.get("tool_name")
        if tool_name is not None and (
            not isinstance(tool_name, str) or not 1 <= len(tool_name) <= 160
        ):
            raise TransformViolation("tool name is invalid")
        if hook is Hook.PRE_TOOL:
            if phase is None or tool_name is None:
                raise TransformViolation("pre-tool hook requires phase and tool")
        elif phase is not None or tool_name is not None:
            raise TransformViolation("phase and tool are only legal for pre-tool")
        subject = value["subject"]
        if not isinstance(subject, str) or len(subject.encode("utf-8")) > 1_048_576:
            raise TransformViolation("hook subject is invalid")
        now_ms = _integer(value["now_ms"], "now_ms", 0)
        deadline_ms = _integer(value["deadline_ms"], "deadline_ms", 1)
        if deadline_ms <= now_ms:
            raise TransformViolation("hook deadline must be in the future")
        return cls(
            call_id=Id(value["call_id"]),
            declaration_id=Id(value["declaration_id"]),
            scope=Scope.from_wire(_mapping(value["scope"], "scope")),
            hook=hook,
            phase=phase,
            tool_name=tool_name,
            subject=subject,
            now_ms=now_ms,
            deadline_ms=deadline_ms,
            trace=Trace.from_wire(_mapping(value["trace"], "trace")),
        )


class TransformProvider:
    def __init__(self, state_root: Path) -> None:
        self._state = _DurableState(state_root / "transform-state.json")

    def handle(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        if method == "describe":
            _keys(params, set())
            return {
                "implementation_version": "hypermid-python-1",
                "majors": [
                    {
                        "version": ROLE_VERSION,
                        "ops": list(OPERATIONS),
                        "stability": "alpha",
                    }
                ],
            }
        if method == "declare":
            return self._declare(params)
        if method == "hook":
            return self._hook(params)
        raise TransformViolation("unsupported transform operation")

    def _declare(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        _keys(params, {"preset", "parameters", "composition", "configuration"})
        preset = params["preset"]
        if not isinstance(preset, str) or not 1 <= len(preset) <= 160:
            raise TransformViolation("declaration preset is invalid")
        configuration = _mapping(params["configuration"], "configuration")
        suffix = configuration.get("suffix", "")
        if not isinstance(suffix, str) or len(suffix.encode("utf-8")) > 4096:
            raise TransformViolation("configured suffix is invalid")
        declaration_id = (
            f"trf-{hashlib.sha256(_canonical_json(params)).hexdigest()[:24]}"
        )
        declaration = {
            "declaration_id": declaration_id,
            "pure": True,
            "subscriptions": [
                {
                    "hook": "pre_tool",
                    "phase": "mutate",
                    "tools": ["lookup"],
                    "ops": ["append"],
                    "on_unavailable": "fail_step",
                    "budget_ms": 500,
                }
            ],
        }
        state = self._state.read()
        declarations = state.setdefault("declarations", {})
        if not isinstance(declarations, dict):
            raise TransformViolation("durable transform declarations are malformed")
        if declarations.get(declaration_id) != {"suffix": suffix}:
            declarations[declaration_id] = {"suffix": suffix}
            self._state.write(state)
        return declaration

    def _hook(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        request = HookRequest.from_wire(params)
        if request.hook is not Hook.PRE_TOOL or request.phase is not Phase.MUTATE:
            raise TransformViolation("hook is outside the provider declaration")
        if request.tool_name != "lookup" or request.deadline_ms - request.now_ms > 500:
            raise TransformViolation("hook exceeds declared tool or budget bounds")
        state = self._state.read()
        declarations = state.get("declarations")
        declaration = (
            declarations.get(str(request.declaration_id))
            if isinstance(declarations, dict)
            else None
        )
        if not isinstance(declaration, dict) or not isinstance(
            declaration.get("suffix"), str
        ):
            raise TransformViolation("hook references an unknown declaration")
        suffix = declaration["suffix"]
        answer: Mapping[str, Any] = {
            "kind": "text",
            "operation": "append",
            "text": suffix,
        }
        state[str(request.call_id)] = {
            "point": "answer_recorded",
            "answer": answer,
            "request_id": str(request.trace.request_id),
        }
        self._state.write(state)
        return answer


class _DurableState:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TransformViolation("durable transform state is malformed")
        return value

    def write(self, value: Mapping[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, _canonical_json(value))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _serve(state_root: Path, crash_at: str | None) -> None:
    if crash_at is not None:
        _startup_crash_point(state_root, crash_at)
    provider = TransformProvider(state_root)
    for line in sys.stdin:
        request_id = "unknown-request"
        try:
            envelope = _mapping(json.loads(line), "request envelope")
            _keys(
                envelope,
                {"protocol", "role_version", "method", "params", "trace"},
                {"scope"},
            )
            if (
                envelope["protocol"] != "hypermid.v1"
                or envelope["role_version"] != ROLE_VERSION
            ):
                raise TransformViolation("incompatible transform protocol")
            trace = _mapping(envelope["trace"], "trace")
            request_id = str(Id(trace.get("request_id")))
            result = provider.handle(
                str(envelope["method"]), _mapping(envelope["params"], "params")
            )
            response = {"request_id": request_id, "result": result}
        except Exception as exc:
            response = {
                "request_id": request_id,
                "error": Error("TRANSFORM_INVALID", str(exc), False).to_wire(),
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def _startup_crash_point(state_root: Path, name: str) -> None:
    if not name or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in name
    ):
        raise TransformViolation("crash point name is invalid")
    points = state_root / "points"
    points.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = points / name
    if marker.exists():
        return
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b"recorded\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(points, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    while True:
        time.sleep(1)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TransformViolation(f"{label} must be an object")
    return value


def _keys(
    value: Mapping[str, Any], required: set[str], optional: set[str] | None = None
) -> None:
    optional = optional or set()
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise TransformViolation("object has missing or unknown fields")


def _integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TransformViolation(f"{label} is invalid")
    if maximum is not None and value > maximum:
        raise TransformViolation(f"{label} is invalid")
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--crash-at")
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    root_value = os.environ.get("HYPERMID_ROLE_STATE_ROOT")
    if not root_value:
        raise SystemExit("HYPERMID_ROLE_STATE_ROOT is required")
    _serve(Path(root_value), args.crash_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Hook",
    "HookRequest",
    "OPERATIONS",
    "Phase",
    "ROLE_VERSION",
    "TextOperation",
    "TransformProvider",
    "TransformSubscription",
    "TransformViolation",
    "UnavailablePolicy",
]
