from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gideon.hypermid.bus import BusMessage
from gideon.hypermid.compaction import CompactionProvider
from gideon.hypermid.foundation import Cursor, Digest, Error, Id, Scope, Trace
from gideon.hypermid.foundation_service import (
    FoundationLocalBus,
    FoundationService,
    RoleOutcome,
)
from gideon.hypermid.observability import (
    DailySegmentWriter,
    LogFilter,
    LogLevel,
    LogParseError,
    RedactionPolicy,
    parse_line,
)
from gideon.hypermid.transforms import TransformProvider


def _trace(name: str) -> Trace:
    return Trace(Id("integration-trace"), Id(name))


def test_authored_trace_level_survives_durable_foundation_logging(
    tmp_path: Path,
) -> None:
    scope = Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))
    policy = LogFilter.parse("hypermid=trace,*=info")
    level = LogLevel.parse("trace")
    assert not policy.malformed
    assert policy.enabled("hypermid.foundation", level)
    assert not policy.enabled("other.component", level)
    bus = FoundationLocalBus.open(tmp_path / "bus", scope)
    service = FoundationService.open(
        tmp_path / "service",
        scope,
        bus=bus,
        compaction=CompactionProvider(tmp_path / "compaction"),
        transform=TransformProvider(tmp_path / "transform"),
        log_writer=DailySegmentWriter(tmp_path / "logs", maximum_bytes=1_048_576),
    )
    try:
        result = service.emit_trace(
            scope,
            _trace("trace-level-request"),
            {"detail": "trace detail"},
            timestamp="2026-10-07T00:00:00.000Z",
            utc_date="2026-10-07",
            level=level,
        )
        assert isinstance(result, Cursor)
    finally:
        service.close()
        bus.close()
    line = (tmp_path / "logs/hypermid-2026-10-07.log").read_bytes()
    encoded = json.loads(line)
    assert encoded["level"] == "trace"
    record = parse_line(line)
    assert record.level == level
    assert record.fields["detail"] == "trace detail"
    encoded["level"] = "unsupported-level"
    with pytest.raises(LogParseError):
        parse_line(json.dumps(encoded).encode() + b"\n")


def _step(session_handle: str) -> dict[str, object]:
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
    delivered_bytes = len(
        json.dumps(messages, sort_keys=True, separators=(",", ":")).encode()
    )
    return {
        "session_handle": session_handle,
        "request_id": "compaction-request",
        "lineage_id": "lineage-1",
        "step_id": "step-1",
        "step_kind": "model_request",
        "geometry": {"context_window": 16_384, "output_limit": 2_048},
        "estimate": {
            "input_bytes": 100,
            "input_tokens": 25,
            "reserved_output_tokens": 128,
        },
        "delta": {
            "after": {"epoch": 1, "sequence": 0},
            "messages": messages,
            "next": {"epoch": 1, "sequence": 1},
            "byte_cap": 65_536,
            "delivered_bytes": delivered_bytes,
            "truncated": False,
        },
        "now_ms": 1_000,
        "deadline_ms": 2_000,
    }


def test_complete_oss_foundation_journey_uses_real_durable_components(
    tmp_path: Path,
) -> None:
    scope = Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))
    other_scope = Scope(Id("owner-1"), Id("project-2"), Id("workspace-1"))
    payload = b'{"event":"ready"}'
    bus = FoundationLocalBus.open(tmp_path / "bus", scope)
    service = FoundationService.open(
        tmp_path / "service",
        scope,
        bus=bus,
        compaction=CompactionProvider(tmp_path / "compaction"),
        transform=TransformProvider(tmp_path / "transform"),
        log_writer=DailySegmentWriter(tmp_path / "logs", maximum_bytes=1_048_576),
        redaction=RedactionPolicy(("secret-value",)),
    )
    try:
        roles = service.discover_roles(scope)
        assert not isinstance(roles, Error)
        assert {(role.role, role.version) for role in roles} == {
            ("compaction", "hypermid.compaction/v1"),
            ("transform", "hypermid.transform/v1"),
        }
        assert all(not hasattr(role, "provider_id") for role in roles)

        message = BusMessage(
            subject="hypermid.owner-1.module.events.v1",
            id=Id("bus-message-1"),
            digest=Digest.sha256(payload),
            headers={"content-type": "application/json"},
            scope=scope,
            trace=_trace("bus-request"),
        )
        exchanged = service.exchange(scope, message, payload)
        assert isinstance(exchanged, Cursor)

        setup = service.invoke_compaction(
            scope,
            "setup",
            {
                "preset": "bounded",
                "parameters": {},
                "composition": {"summary": "v1"},
                "configuration": {"max_messages": 32},
            },
            _trace("compaction-setup"),
        )
        assert isinstance(setup, RoleOutcome)
        step = service.invoke_compaction(
            scope,
            "step",
            _step(str(setup.value["session_handle"])),
            _trace("compaction-step"),
        )
        assert isinstance(step, RoleOutcome)
        assert step.value["cursor"] == {"epoch": 1, "sequence": 1}

        declaration = service.invoke_transform(
            scope,
            "declare",
            {
                "preset": "safe-append",
                "parameters": {},
                "composition": {"provider": "local"},
                "configuration": {"suffix": " [checked]"},
            },
            _trace("transform-declare"),
        )
        assert isinstance(declaration, RoleOutcome)
        transformed = service.invoke_transform(
            scope,
            "hook",
            {
                "call_id": "hook-1",
                "declaration_id": declaration.value["declaration_id"],
                "scope": scope.to_wire(),
                "hook": "pre_tool",
                "phase": "mutate",
                "tool_name": "lookup",
                "subject": "query",
                "now_ms": 1_000,
                "deadline_ms": 1_500,
                "trace": _trace("transform-hook-provider").to_wire(),
            },
            _trace("transform-hook"),
        )
        assert isinstance(transformed, RoleOutcome)
        assert transformed.value == {
            "kind": "text",
            "operation": "append",
            "text": " [checked]",
        }

        traced = service.emit_trace(
            scope,
            _trace("trace-request"),
            {"api_key": "secret-value", "detail": "token=secret-value"},
            timestamp="2026-10-02T12:00:00.000Z",
            utc_date="2026-10-02",
        )
        assert isinstance(traced, Cursor)
        line = (tmp_path / "logs/hypermid-2026-10-02.log").read_text()
        assert "secret-value" not in line
        assert "[REDACTED]" in line

        denied = service.discover_roles(other_scope)
        assert isinstance(denied, Error)
        assert denied.code == "AUTHORIZATION_DENIED"
    finally:
        service.close()
        bus.close()

    with sqlite3.connect(tmp_path / "bus/foundation-bus.sqlite3") as connection:
        row = connection.execute(
            "SELECT message_id, digest, payload FROM hypermid_foundation_bus"
        ).fetchone()
    assert row == ("bus-message-1", str(Digest.sha256(payload)), payload)
