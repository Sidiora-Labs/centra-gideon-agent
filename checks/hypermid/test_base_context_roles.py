from __future__ import annotations

import json
from pathlib import Path

import pytest

from gideon.hypermid.compaction import (
    CompactionProvider,
    CompactionViolation,
    MessageDelta,
    ROLE_VERSION as COMPACTION_ROLE,
)
from gideon.hypermid.foundation import Cursor, Id, Scope, Trace
from gideon.hypermid.transforms import (
    Hook,
    HookRequest,
    Phase,
    ROLE_VERSION as TRANSFORM_ROLE,
    TextOperation,
    TransformProvider,
    TransformSubscription,
    TransformViolation,
    UnavailablePolicy,
)


ROOT = Path(__file__).resolve().parents[2]


def _canonical_size(value: object) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


def _delta() -> dict[str, object]:
    messages = [
        {
            "cursor": {"epoch": 1, "sequence": 1},
            "message": {
                "message_id": "message-1",
                "ordinal": 0,
                "role": "user",
                "content": "retain this",
            },
        }
    ]
    return {
        "after": {"epoch": 1, "sequence": 0},
        "messages": messages,
        "next": {"epoch": 1, "sequence": 1},
        "byte_cap": 65_536,
        "delivered_bytes": _canonical_size(messages),
        "truncated": False,
    }


def _step(session_handle: str, request_id: str = "request-1") -> dict[str, object]:
    return {
        "session_handle": session_handle,
        "request_id": request_id,
        "lineage_id": "lineage-1",
        "step_id": "step-1",
        "step_kind": "model_request",
        "geometry": {"context_window": 16_384, "output_limit": 2_048},
        "estimate": {
            "input_bytes": 100,
            "input_tokens": 25,
            "reserved_output_tokens": 128,
        },
        "delta": _delta(),
        "now_ms": 1_000,
        "deadline_ms": 2_000,
    }


def test_compaction_setup_is_pure_and_step_cursor_is_durable(tmp_path: Path) -> None:
    provider = CompactionProvider(tmp_path)
    setup = {
        "preset": "bounded",
        "parameters": {},
        "composition": {"summary": "v1"},
        "configuration": {"max_messages": 32},
    }
    first = provider.handle("setup", setup)
    second = provider.handle("setup", setup)
    assert first == second
    assert provider.handle("describe", {})["majors"][0] == {
        "version": COMPACTION_ROLE,
        "ops": ["describe", "ready", "setup", "step"],
        "stability": "alpha",
    }

    answer = provider.handle("step", _step(first["session_handle"]))
    assert answer == {
        "kind": "no_change",
        "request_id": "request-1",
        "cursor": {"epoch": 1, "sequence": 1},
    }
    persisted = json.loads((tmp_path / "compaction-state.json").read_text())
    assert persisted["newest_request_id"] == "request-1"
    assert persisted["provider_cursor"] == {"epoch": 1, "sequence": 1}


def test_compaction_delta_refuses_duplicates_and_cursor_overrun() -> None:
    delta = _delta()
    delta["next"] = {"epoch": 1, "sequence": 2}
    with pytest.raises(CompactionViolation, match="last delivered"):
        MessageDelta.from_wire(delta)

    duplicate = _delta()
    messages = list(duplicate["messages"])
    messages.append(
        {
            "cursor": {"epoch": 1, "sequence": 2},
            "message": {
                "message_id": "message-1",
                "ordinal": 1,
                "role": "assistant",
                "content": "duplicate Id",
            },
        }
    )
    duplicate["messages"] = messages
    duplicate["next"] = {"epoch": 1, "sequence": 2}
    duplicate["delivered_bytes"] = _canonical_size(messages)
    with pytest.raises(CompactionViolation, match="unique"):
        MessageDelta.from_wire(duplicate)


def test_transform_declaration_and_hook_stay_inside_frozen_bounds(tmp_path: Path) -> None:
    provider = TransformProvider(tmp_path)
    declaration_request = {
        "preset": "safe-append",
        "parameters": {},
        "composition": {"provider": "local"},
        "configuration": {"suffix": " [checked]"},
    }
    first = provider.handle("declare", declaration_request)
    assert first == provider.handle("declare", declaration_request)
    assert provider.handle("describe", {})["majors"][0]["version"] == TRANSFORM_ROLE

    hook = {
        "call_id": "hook-1",
        "declaration_id": first["declaration_id"],
        "scope": {"owner_id": "owner-1", "project_id": "project-1"},
        "hook": "pre_tool",
        "phase": "mutate",
        "tool_name": "lookup",
        "subject": "query",
        "now_ms": 1_000,
        "deadline_ms": 1_500,
        "trace": {"trace_id": "trace-1", "request_id": "request-1"},
    }
    assert provider.handle("hook", hook) == {
        "kind": "text",
        "operation": "append",
        "text": " [checked]",
    }
    durable = json.loads((tmp_path / "transform-state.json").read_text())
    assert durable["hook-1"]["point"] == "answer_recorded"


def test_transform_phase_questions_grants_and_failure_policy_are_explicit() -> None:
    subscription = TransformSubscription(
        hook=Hook.PRE_TOOL,
        phase=Phase.APPROVE,
        tools=frozenset({"lookup"}),
        ops=frozenset({TextOperation.APPEND}),
        on_unavailable=UnavailablePolicy.FAIL_RUN,
        budget_ms=500,
    )
    assert subscription.to_wire()["on_unavailable"] == "fail_run"

    request = HookRequest.from_wire(
        {
            "call_id": "hook-2",
            "declaration_id": "declaration-2",
            "scope": Scope(Id("owner-1"), Id("project-1")).to_wire(),
            "hook": "pre_tool",
            "phase": "approve",
            "tool_name": "lookup",
            "subject": "query",
            "now_ms": 1_000,
            "deadline_ms": 1_500,
            "trace": Trace(Id("trace-1"), Id("request-2")).to_wire(),
        }
    )
    assert request.phase is Phase.APPROVE
    with pytest.raises(TransformViolation, match="only legal for pre-tool"):
        TransformSubscription(
            hook=Hook.POST_TOOL,
            phase=Phase.MUTATE,
            tools=frozenset(),
            ops=frozenset({TextOperation.REPLACE}),
            on_unavailable=UnavailablePolicy.CONTINUE,
            budget_ms=100,
        )


def test_independent_role_vectors_pin_all_non_draft_choices() -> None:
    compaction = json.loads(
        (ROOT / "crates/hypermid-role-vectors/compaction-v1/cases.json").read_text()
    )
    transform = json.loads(
        (ROOT / "crates/hypermid-role-vectors/transform-v1/cases.json").read_text()
    )
    assert {case["name"] for case in compaction["directives"]} >= {
        "half-open replacement",
        "empty insertion range",
        "zero version remains draft and is refused",
    }
    assert {case["name"] for case in transform["subscriptions"]} >= {
        "phase outside pre-tool is refused",
        "unbounded budget is refused",
    }
    assert any(not case["valid"] for case in compaction["directives"])
    assert any(not case["valid"] for case in transform["subscriptions"])
