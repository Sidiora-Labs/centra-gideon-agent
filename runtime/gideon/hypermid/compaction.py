from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .foundation import ContractViolation, Cursor, Error, Id


ROLE_VERSION = "hypermid.compaction/v1"
OPERATIONS = ("describe", "ready", "setup", "step")
MAX_DELTA_BYTES = 1_048_576
MAX_WAIT_MS = 60_000


class CompactionViolation(ContractViolation):
    pass


@dataclass(frozen=True, slots=True)
class MessageDelta:
    after: Cursor | None
    messages: tuple[Mapping[str, Any], ...]
    next: Cursor | None
    byte_cap: int
    delivered_bytes: int
    truncated: bool

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> MessageDelta:
        _keys(
            value,
            {"messages", "byte_cap", "delivered_bytes", "truncated"},
            {"after", "next"},
        )
        messages = value["messages"]
        if not isinstance(messages, list):
            raise CompactionViolation("delta messages must be an array")
        instance = cls(
            after=Cursor.from_wire(value["after"]) if value.get("after") is not None else None,
            messages=tuple(_mapping(item, "delta message") for item in messages),
            next=Cursor.from_wire(value["next"]) if value.get("next") is not None else None,
            byte_cap=_integer(value["byte_cap"], "byte_cap", 1, MAX_DELTA_BYTES),
            delivered_bytes=_integer(
                value["delivered_bytes"], "delivered_bytes", 0, MAX_DELTA_BYTES
            ),
            truncated=_boolean(value["truncated"], "truncated"),
        )
        instance.validate()
        return instance

    def validate(self) -> None:
        cursors: list[Cursor] = []
        message_ids: set[str] = set()
        previous = self.after
        for item in self.messages:
            _keys(item, {"cursor", "message"})
            cursor = Cursor.from_wire(_mapping(item["cursor"], "message cursor"))
            message = _mapping(item["message"], "role message")
            message_id = str(Id(message.get("message_id")))
            if previous is not None and cursor <= previous:
                raise CompactionViolation("delta cursors must rise strictly")
            if previous is not None and cursor.epoch != previous.epoch:
                raise CompactionViolation("delta cursors cannot cross epochs")
            if message_id in message_ids:
                raise CompactionViolation("delta message Ids must be unique")
            cursors.append(cursor)
            message_ids.add(message_id)
            previous = cursor
        if self.next != previous:
            raise CompactionViolation("delta cursor must stop at the last delivered message")
        encoded = json.dumps(
            list(self.messages), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(encoded) != self.delivered_bytes or self.delivered_bytes > self.byte_cap:
            raise CompactionViolation("delta byte accounting is invalid")


@dataclass(frozen=True, slots=True)
class StepRequest:
    request_id: Id
    session_handle: Id
    now_ms: int
    deadline_ms: int
    delta: MessageDelta
    raw: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> StepRequest:
        required = {
            "session_handle",
            "request_id",
            "lineage_id",
            "step_id",
            "step_kind",
            "geometry",
            "estimate",
            "delta",
            "now_ms",
            "deadline_ms",
        }
        optional = {
            "previous_usage",
            "provider_failure",
            "rebuild_reason",
            "newest_message",
            "last_applied",
            "last_rejected",
        }
        _keys(value, required, optional)
        now_ms = _integer(value["now_ms"], "now_ms", 0)
        deadline_ms = _integer(value["deadline_ms"], "deadline_ms", 1)
        if deadline_ms <= now_ms:
            raise CompactionViolation("step deadline must be in the future")
        geometry = _mapping(value["geometry"], "model geometry")
        context_window = _integer(geometry.get("context_window"), "context_window", 1)
        output_limit = _integer(geometry.get("output_limit"), "output_limit", 1)
        if output_limit > context_window:
            raise CompactionViolation("output limit exceeds context window")
        return cls(
            request_id=Id(value["request_id"]),
            session_handle=Id(value["session_handle"]),
            now_ms=now_ms,
            deadline_ms=deadline_ms,
            delta=MessageDelta.from_wire(_mapping(value["delta"], "delta")),
            raw=value,
        )


class CompactionProvider:
    def __init__(self, state_root: Path) -> None:
        self._state = _DurableState(state_root / "compaction-state.json")

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
        if method == "setup":
            return self._setup(params)
        if method == "step":
            return self._step(params)
        if method == "ready":
            return self._ready(params)
        raise CompactionViolation("unsupported compaction operation")

    def _setup(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        _keys(params, {"preset", "parameters", "composition", "configuration"})
        preset = params["preset"]
        if not isinstance(preset, str) or not 1 <= len(preset) <= 160:
            raise CompactionViolation("setup preset is invalid")
        canonical = _canonical_json(params)
        session_handle = Id(f"cmp-{hashlib.sha256(canonical).hexdigest()[:24]}")
        state = self._state.read()
        sessions = state.setdefault("sessions", {})
        if not isinstance(sessions, dict):
            raise CompactionViolation("durable setup sessions are malformed")
        if sessions.get(str(session_handle)) != hashlib.sha256(canonical).hexdigest():
            sessions[str(session_handle)] = hashlib.sha256(canonical).hexdigest()
            self._state.write(state)
        return {
            "session_handle": str(session_handle),
            "stability_ranks": [{"name": "stable", "rank": 1}],
            "call_conditions": [{"kind": "context_pressure", "parameters": {}}],
        }

    def _step(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        request = StepRequest.from_wire(params)
        state = self._state.read()
        sessions = state.get("sessions")
        if not isinstance(sessions, dict) or str(request.session_handle) not in sessions:
            raise CompactionViolation("step references an unknown setup session")
        state["newest_request_id"] = str(request.request_id)
        state["deadline_ms"] = request.deadline_ms
        state["provider_cursor"] = (
            request.delta.next.to_wire() if request.delta.next is not None else None
        )
        self._state.write(state)
        cursor = request.delta.next or request.delta.after or Cursor(1, 0)
        return {
            "kind": "no_change",
            "request_id": str(request.request_id),
            "cursor": cursor.to_wire(),
        }

    def _ready(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        _keys(params, {"session_handle", "request_id", "wait_id", "now_ms"})
        Id(params["session_handle"])
        Id(params["request_id"])
        Id(params["wait_id"])
        _integer(params["now_ms"], "now_ms", 0)
        state = self._state.read()
        waiting = state.get("waiting")
        return {
            "ready": bool(
                isinstance(waiting, dict)
                and waiting.get("request_id") == params["request_id"]
                and waiting.get("wait_id") == params["wait_id"]
            )
        }


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
            raise CompactionViolation("durable compaction state is malformed")
        return value

    def write(self, value: Mapping[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        payload = _canonical_json(value)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, payload)
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
    provider = CompactionProvider(state_root)
    for line in sys.stdin:
        request_id = "unknown-request"
        try:
            envelope = _mapping(json.loads(line), "request envelope")
            _keys(envelope, {"protocol", "role_version", "method", "params", "trace"}, {"scope"})
            if envelope["protocol"] != "hypermid.v1" or envelope["role_version"] != ROLE_VERSION:
                raise CompactionViolation("incompatible compaction protocol")
            trace = _mapping(envelope["trace"], "trace")
            request_id = str(Id(trace.get("request_id")))
            result = provider.handle(
                str(envelope["method"]), _mapping(envelope["params"], "params")
            )
            response = {"request_id": request_id, "result": result}
        except Exception as exc:
            response = {
                "request_id": request_id,
                "error": Error("COMPACTION_INVALID", str(exc), False).to_wire(),
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def _startup_crash_point(state_root: Path, name: str) -> None:
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in name):
        raise CompactionViolation("crash point name is invalid")
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
        raise CompactionViolation(f"{label} must be an object")
    return value


def _keys(
    value: Mapping[str, Any], required: set[str], optional: set[str] | None = None
) -> None:
    optional = optional or set()
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise CompactionViolation("object has missing or unknown fields")


def _integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CompactionViolation(f"{label} is invalid")
    if maximum is not None and value > maximum:
        raise CompactionViolation(f"{label} is invalid")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise CompactionViolation(f"{label} must be a boolean")
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


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
    "CompactionProvider",
    "CompactionViolation",
    "MessageDelta",
    "OPERATIONS",
    "ROLE_VERSION",
    "StepRequest",
]
