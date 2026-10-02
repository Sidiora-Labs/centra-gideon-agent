"""Typed Gideon adapter for Hypermid's recoverable context-reduction tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .models import MAX_SAFE_INTEGER, Cursor, JsonValue, Scope, Trace

if TYPE_CHECKING:
    from .client import HypermidClient


MAX_REDUCTION_TARGETS = 100_000
_STATUSES = frozenset({"queued", "applied", "rejected", "already_applied"})


class ReductionToolError(ValueError):
    pass


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReductionToolError(f"{field} must be an object")
    return value


def _require_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_SAFE_INTEGER:
        raise ReductionToolError(f"{field} is outside the Hypermid integer range")
    return value


def _require_id(value: object, field: str) -> str:
    from .foundation import Id

    try:
        return str(Id(value))
    except (TypeError, ValueError) as exc:
        raise ReductionToolError(f"{field} is not a valid Hypermid id") from exc


def _reject_source_content(value: object) -> None:
    if isinstance(value, Mapping):
        if "source_content" in value:
            raise ReductionToolError("reduction markers cannot contain authoritative source content")
        for item in value.values():
            _reject_source_content(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_source_content(item)


@dataclass(frozen=True, slots=True)
class ReductionTarget:
    tag_start: int
    tag_end: int

    def __post_init__(self) -> None:
        _require_int(self.tag_start, "tag_start", minimum=1)
        _require_int(self.tag_end, "tag_end", minimum=1)
        if self.tag_start > self.tag_end:
            raise ReductionToolError("tag_start must not exceed tag_end")

    @property
    def size(self) -> int:
        return self.tag_end - self.tag_start + 1

    def to_wire(self) -> dict[str, int]:
        return {"tag_start": self.tag_start, "tag_end": self.tag_end}


@dataclass(frozen=True, slots=True)
class ReductionCommand:
    scope: Scope
    session_id: str
    expected_cursor: Cursor
    writer_lease: Mapping[str, JsonValue]
    idempotency_key: str
    targets: tuple[ReductionTarget, ...]
    trace: Trace

    def __post_init__(self) -> None:
        _require_id(self.session_id, "session_id")
        _require_id(self.idempotency_key, "idempotency_key")
        if not 1 <= len(self.targets) <= 1_024:
            raise ReductionToolError("targets must contain between 1 and 1024 ranges")
        expanded = set[int]()
        for target in self.targets:
            if target.size > MAX_REDUCTION_TARGETS:
                raise ReductionToolError("target range exceeds the reduction bound")
            expanded.update(range(target.tag_start, target.tag_end + 1))
            if len(expanded) > MAX_REDUCTION_TARGETS:
                raise ReductionToolError("targets exceed the reduction bound")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "scope": self.scope.to_wire(),
            "session_id": self.session_id,
            "expected_cursor": self.expected_cursor.to_wire(),
            "writer_lease": dict(self.writer_lease),
            "idempotency_key": self.idempotency_key,
            "targets": [target.to_wire() for target in self.targets],
            "trace": self.trace.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class ReductionOutcome:
    tag: int
    status: str
    reason_code: str
    item_id: str | None
    estimated_tokens: int | None
    marker: Mapping[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class ReductionResult:
    scope: Scope
    session_id: str
    cursor: Cursor
    idempotency_key: str
    outcomes: tuple[ReductionOutcome, ...]
    trace: Trace


def parse_reduction_result(value: object) -> ReductionResult:
    raw = _require_mapping(value, "reduction result")
    required = {"scope", "session_id", "cursor", "idempotency_key", "outcomes", "trace"}
    optional = {"boundary"}
    if set(raw) - required - optional or not required <= set(raw):
        raise ReductionToolError("reduction result fields do not match the contract")
    outcomes_raw = raw["outcomes"]
    if not isinstance(outcomes_raw, list) or not 1 <= len(outcomes_raw) <= MAX_REDUCTION_TARGETS:
        raise ReductionToolError("reduction outcomes are outside the bounded result size")
    outcomes: list[ReductionOutcome] = []
    for index, value in enumerate(outcomes_raw):
        item = _require_mapping(value, f"outcomes[{index}]")
        allowed = {"tag", "status", "reason_code", "item_id", "estimated_tokens", "marker"}
        if set(item) - allowed or not {"tag", "status", "reason_code"} <= set(item):
            raise ReductionToolError(f"outcomes[{index}] fields do not match the contract")
        status = item["status"]
        reason = item["reason_code"]
        if status not in _STATUSES or not isinstance(reason, str) or not reason:
            raise ReductionToolError(f"outcomes[{index}] has an invalid status or reason")
        marker = item.get("marker")
        if status == "applied" and not isinstance(marker, Mapping):
            raise ReductionToolError("an applied reduction must include its recovery marker")
        if marker is not None:
            _reject_source_content(marker)
        outcomes.append(
            ReductionOutcome(
                tag=_require_int(item["tag"], f"outcomes[{index}].tag", minimum=1),
                status=status,
                reason_code=reason,
                item_id=(
                    _require_id(item["item_id"], f"outcomes[{index}].item_id")
                    if "item_id" in item
                    else None
                ),
                estimated_tokens=(
                    _require_int(item["estimated_tokens"], f"outcomes[{index}].estimated_tokens")
                    if "estimated_tokens" in item
                    else None
                ),
                marker=dict(marker) if isinstance(marker, Mapping) else None,
            )
        )
    return ReductionResult(
        scope=Scope.from_wire(raw["scope"]),
        session_id=_require_id(raw["session_id"], "session_id"),
        cursor=Cursor.from_wire(raw["cursor"]),
        idempotency_key=_require_id(raw["idempotency_key"], "idempotency_key"),
        outcomes=tuple(outcomes),
        trace=Trace.from_wire(raw["trace"]),
    )


class HypermidReductionTool:
    def __init__(self, client: HypermidClient) -> None:
        self._client = client

    async def reduce(self, command: ReductionCommand) -> ReductionResult:
        response = await self._client.request(
            "reduce",
            command.to_wire(),
            trace=command.trace,
            effect_kind="idempotent",
            scope=command.scope,
        )
        result = parse_reduction_result(response)
        if result.scope != command.scope or result.session_id != command.session_id:
            raise ReductionToolError("daemon returned a foreign reduction result")
        if result.idempotency_key != command.idempotency_key or result.trace != command.trace:
            raise ReductionToolError("daemon returned a reduction result for another request")
        return result


def context_reduction_tool_schema() -> dict[str, JsonValue]:
    return {
        "name": "context_reduce",
        "description": (
            "Queue recoverable removal of context by stable reclaim tag. "
            "Protected current work is rejected and original history remains available through expansion."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["targets"],
            "properties": {
                "targets": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1024,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tag_start", "tag_end"],
                        "properties": {
                            "tag_start": {"type": "integer", "minimum": 1},
                            "tag_end": {"type": "integer", "minimum": 1},
                        },
                    },
                }
            },
        },
    }


def targets_from_tool_input(value: object) -> tuple[ReductionTarget, ...]:
    raw = _require_mapping(value, "tool input")
    if set(raw) != {"targets"} or not isinstance(raw["targets"], Sequence) or isinstance(
        raw["targets"], (str, bytes)
    ):
        raise ReductionToolError("tool input must contain only a targets array")
    targets: list[ReductionTarget] = []
    for index, value in enumerate(raw["targets"]):
        target = _require_mapping(value, f"targets[{index}]")
        if set(target) != {"tag_start", "tag_end"}:
            raise ReductionToolError(f"targets[{index}] fields do not match the contract")
        targets.append(ReductionTarget(target["tag_start"], target["tag_end"]))
    return tuple(targets)


__all__ = [
    "HypermidReductionTool",
    "ReductionCommand",
    "ReductionOutcome",
    "ReductionResult",
    "ReductionTarget",
    "ReductionToolError",
    "context_reduction_tool_schema",
    "parse_reduction_result",
    "targets_from_tool_input",
]
