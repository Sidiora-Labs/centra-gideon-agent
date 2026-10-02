from __future__ import annotations

import pytest

from gideon.hypermid.foundation import Cursor, Digest, Id, Scope, Trace
from gideon.hypermid.tools import (
    ReductionCommand,
    ReductionTarget,
    ReductionToolError,
    parse_reduction_result,
    targets_from_tool_input,
)


def test_reduction_wire_adapter_keeps_recovery_identity_without_source_content() -> None:
    scope = Scope(Id("owner-1"), Id("project-1"))
    trace = Trace(Id("trace-1"), Id("request-1"))
    targets = targets_from_tool_input(
        {"targets": [{"tag_start": 4, "tag_end": 4}, {"tag_start": 8, "tag_end": 9}]}
    )
    command = ReductionCommand(
        scope=scope,
        session_id="session-1",
        expected_cursor=Cursor(1, 9),
        writer_lease={"lease_id": "lease-1"},
        idempotency_key="reduce-1",
        targets=targets,
        trace=trace,
    )
    assert command.to_wire()["targets"] == [
        {"tag_start": 4, "tag_end": 4},
        {"tag_start": 8, "tag_end": 9},
    ]

    source_digest = Digest.sha256(b"authoritative source bytes")
    result = parse_reduction_result(
        {
            "scope": scope.to_wire(),
            "session_id": "session-1",
            "cursor": Cursor(1, 10).to_wire(),
            "idempotency_key": "reduce-1",
            "trace": trace.to_wire(),
            "outcomes": [
                {
                    "tag": 4,
                    "status": "applied",
                    "reason_code": "applied_at_compatible_boundary",
                    "item_id": "item-4",
                    "estimated_tokens": 240,
                    "marker": {
                        "marker_id": "reduction-4",
                        "item_id": "item-4",
                        "source_digest": str(source_digest),
                        "reclaim_tag": 4,
                        "content_class": "assistant_prose",
                        "source_bytes": 960,
                        "estimated_tokens": 240,
                        "recovery": {
                            "operation": "expand",
                            "item_id": "item-4",
                            "reclaim_tag": 4,
                            "cursor": Cursor(1, 4).to_wire(),
                            "source_digest": str(source_digest),
                        },
                    },
                },
                {
                    "tag": 8,
                    "status": "rejected",
                    "reason_code": "active_tool_arc",
                    "item_id": "item-8",
                    "estimated_tokens": 100,
                },
                {
                    "tag": 9,
                    "status": "queued",
                    "reason_code": "awaiting_cache_boundary",
                    "item_id": "item-9",
                    "estimated_tokens": 80,
                },
            ],
        }
    )
    assert result.outcomes[0].marker["recovery"]["item_id"] == "item-4"
    assert result.outcomes[1].reason_code == "active_tool_arc"
    assert result.outcomes[2].status == "queued"

    unsafe = result.outcomes[0].marker | {"source_content": "authoritative source bytes"}
    with pytest.raises(ReductionToolError, match="source content"):
        parse_reduction_result(
            {
                "scope": scope.to_wire(),
                "session_id": "session-1",
                "cursor": Cursor(1, 10).to_wire(),
                "idempotency_key": "reduce-1",
                "trace": trace.to_wire(),
                "outcomes": [
                    {
                        "tag": 4,
                        "status": "applied",
                        "reason_code": "applied_at_compatible_boundary",
                        "marker": unsafe,
                    }
                ],
            }
        )


def test_reduction_targets_are_bounded_before_daemon_dispatch() -> None:
    with pytest.raises(ReductionToolError, match="exceeds"):
        ReductionCommand(
            scope=Scope(Id("owner-1"), Id("project-1")),
            session_id="session-1",
            expected_cursor=Cursor(1, 0),
            writer_lease={"lease_id": "lease-1"},
            idempotency_key="reduce-1",
            targets=(ReductionTarget(1, 100_001),),
            trace=Trace(Id("trace-1"), Id("request-1")),
        )
