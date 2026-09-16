"""Tests for the fresh-session resumability audit + MCP replay-as-fake-server (Session 4).

The resume-audit proves a persisted loop can answer done/verified/next/how-to-verify from
disk ALONE (no in-memory session) — the audit that would have caught the dead-resume bugs.
The FakeMcpServer proves a recorded mcp trace replays deterministically offline.
"""

from __future__ import annotations

import pytest

from checks.harness import resume_audit
from checks.harness.replay import FakeMcpServer, TraceEvent
from gideon.automation.loop import files as loop_files
from gideon.automation.loop import store
from gideon.automation.loop.loop import Loop, LoopStatus


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.automation.loop.files.config_dir", lambda: tmp_path)
    return tmp_path


def _goal(**over) -> Loop:
    base = dict(
        id="",
        name="G",
        kind="goal",
        task="investigate the latency regression and propose a fix",
        project_id="p-1",
        kind_config={"goal_type": "open_ended", "granularity": "balanced"},
    )
    base.update(over)
    return store.create(Loop(**base))


def _code(**over) -> Loop:
    base = dict(
        id="",
        name="C",
        kind="code",
        task="add oauth login",
        project_id="p-1",
        plan=[
            {"stage": "design", "title": "Design", "min_cycles": 1},
            {"stage": "build", "title": "Build", "min_cycles": 2},
        ],
        phase_status={"design": "done"},
        kind_config={"entry_stage": "design", "queued_task_ids": []},
    )
    base.update(over)
    return store.create(Loop(**base))


def test_missing_loop_is_not_answerable() -> None:
    r = resume_audit.audit_loop("nonexistent-id")
    assert not r.exists
    assert not r.ok
    assert "not found" in r.failures()[0]


def test_freshly_created_goal_loop_is_fully_answerable() -> None:
    g = _goal()
    r = resume_audit.audit_loop(g.id)
    assert r.exists
    assert r.done_answerable
    assert r.verified_answerable
    assert r.next_answerable
    assert r.how_to_verify_answerable
    assert r.ok


def test_phased_loop_names_next_stage_from_disk() -> None:
    c = _code()
    r = resume_audit.audit_loop(c.id)
    assert r.ok
    assert r.detail["phased"] is True
    assert r.detail["next_stage"] == "build"


def test_resume_after_simulated_restart_uses_disk_only() -> None:
    g = _goal()
    store.update_status(g.id, LoopStatus.RUNNING)
    loop_files.write_verdict(g.id, 1, {"roi": 0.8, "summary": "found the N+1 query"})
    r = resume_audit.audit_loop(g.id)
    assert r.ok
    assert r.detail["status"] == LoopStatus.RUNNING.value
    assert r.detail["verdict_count"] == 1


def test_complete_loop_is_terminal_answerable() -> None:
    g = _goal()
    store.update_status(g.id, LoopStatus.COMPLETE)
    r = resume_audit.audit_loop(g.id)
    assert r.ok
    assert r.detail["terminal_or_attention"] is True


def test_loop_with_blank_task_flags_how_to_verify() -> None:
    g = _goal(task="")
    r = resume_audit.audit_loop(g.id)
    assert not r.how_to_verify_answerable
    assert not r.ok
    assert any("how to verify" in f for f in r.failures())


def _mcp_event(tool: str, arguments: dict, ok: bool, output: str) -> TraceEvent:
    return TraceEvent.from_json(
        {
            "ts": 0.0,
            "stream": "mcp",
            "key": "srv",
            "type": "call_tool",
            "payload": {
                "tool": tool,
                "arguments": arguments,
                "ok": ok,
                "output": output,
            },
        }
    )


def test_fake_mcp_server_replays_recorded_response() -> None:
    server = FakeMcpServer([_mcp_event("search", {"q": "latency"}, True, "3 results")])
    ok, out = server.call_tool("search", {"q": "latency"})
    assert ok is True and out == "3 results"


def test_fake_mcp_server_arg_order_independent() -> None:
    server = FakeMcpServer([_mcp_event("f", {"a": 1, "b": 2}, True, "ok")])
    ok, out = server.call_tool("f", {"b": 2, "a": 1})
    assert ok and out == "ok"


def test_fake_mcp_server_returns_successive_responses() -> None:
    server = FakeMcpServer(
        [
            _mcp_event("t", {}, True, "first"),
            _mcp_event("t", {}, True, "second"),
        ]
    )
    assert server.call_tool("t", {})[1] == "first"
    assert server.call_tool("t", {})[1] == "second"
    assert server.call_tool("t", {})[1] == "second"


def test_fake_mcp_server_miss_surfaces_gap() -> None:
    server = FakeMcpServer([_mcp_event("known", {}, True, "x")])
    ok, out = server.call_tool("unknown", {})
    assert ok is False
    assert "no recorded response" in out
