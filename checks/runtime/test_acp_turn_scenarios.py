"""Black-box turn scenarios — the stdout-boundary oracle for the P9 convergence (task #7).

The ~65 existing client turn tests mock ``_prompt_loop`` to yield ``(action, msg)`` tuples —
white-box tests of the machinery being RETIRED. This module instead scripts RAW JSON-RPC
frames onto a fake stdout and drives the PUBLIC ``stream_events`` API, asserting on the
yielded ``AcpEvent`` stream. That is architecture-agnostic: it exercises whatever reader
the client uses (today the inline ``_read_message``/``_prompt_loop``; after convergence the
FrameRouter+AcpSession path) with ZERO changes — so it is a faithful oracle to guard the
cutover. Each scenario models one real turn shape (text, thinking, tool call+result,
permission, interrupted, stale, error, command).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.types import (
    CAP_COMMANDS,
    EVENT_AGENT_SWITCHED,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    METHOD_AGENT_SWITCHED,
)


class _ScriptedStdout:
    """A fake asyncio StreamReader whose readline() returns pre-scripted JSON-RPC frames
    (one per line), then BLOCKS (like a real idle stdio pipe that stays open).

    Real backend stdout does NOT hit EOF the instant a turn's frames are written — the
    process stays alive for the next turn. Blocking after exhaustion models that: a turn
    completes on its terminal ``result`` frame (or the stale-silence timeout for the
    no-terminal scenarios), never on a synthetic EOF/close. This keeps the FrameRouter
    reader alive across the turn, exactly as in production."""

    def __init__(self, frames: list[dict]):
        self._lines = [(json.dumps(f) + "\n").encode() for f in frames]
        self._i = 0

    async def readline(self) -> bytes:
        if self._i >= len(self._lines):
            await asyncio.sleep(0.3)
            return b""
        line = self._lines[self._i]
        self._i += 1
        return line


def _client_with_frames(
    frames: list[dict], *, req_id: int = 1, session_key: str = "s"
) -> AcpClient:
    """A ready AcpClient wired to a scripted stdout that emits *frames*, driven through
    the REAL turn path (FrameRouter → AcpSession) — the P9#7 wrapper architecture.

    We stub only ``ensure_ready`` (no real subprocess) and inject a live
    :class:`AcpConnection` whose FrameRouter reads the scripted stdout via a fake
    transport, plus one bound :class:`AcpSession` on the well-known ``req_id``. Because
    the turn's request id is deterministic (the fake connection's id counter starts at
    the scripted *req_id* minus one), the terminal frame's ``id`` matches. The scenario
    assertions below are unchanged — only this harness plumbing adapts to the wrapper.
    """
    from gideon.integrations.acp.reader import FrameRouter
    from gideon.integrations.acp.session import AcpConnection

    client = AcpClient(session_key=session_key)

    scripted = _ScriptedStdout(frames)

    class _FakeTransport:
        """Minimal transport: a readline() over the scripted frames + a no-op stdin."""

        def __init__(self):
            self._alive = True

        async def readline(self) -> bytes:
            return await scripted.readline()

        async def write(self, data: str) -> None:
            return None

        def is_alive(self) -> bool:
            return self._alive

    transport = _FakeTransport()
    router = FrameRouter(transport.readline)
    conn = AcpConnection(None, router, dialect=client._dialect, transport=transport)
    conn._next_id = req_id - 1

    session = conn._bind_session("sess-abc", session_files_dir=None)
    router.start()
    conn._agent_capabilities = {CAP_COMMANDS: True}
    client._can_execute_commands = conn.supports_native_commands
    client._connection = conn
    client._session = session
    client._session_id = "sess-abc"
    client.ensure_ready = AsyncMock()
    return client


async def _collect(client: AcpClient, message: str = "go") -> list:
    return [ev async for ev in client.stream_events(message)]


_SID = "sess-abc"


def _text(t: str, kind: str = "text") -> dict:
    return {
        "method": "session/update",
        "params": {
            "sessionId": _SID,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": kind, "text": t},
            },
        },
    }


def _tool_call(
    tid: str, title: str, kind: str = "read", raw: dict | None = None
) -> dict:
    return {
        "method": "session/update",
        "params": {
            "sessionId": _SID,
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": tid,
                "title": title,
                "kind": kind,
                "rawInput": raw or {"path": "/x"},
            },
        },
    }


def _tool_update(tid: str, *, status: str = "completed", output: str = "done") -> dict:
    return {
        "method": "session/update",
        "params": {
            "sessionId": _SID,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tid,
                "status": status,
                "content": [
                    {"type": "content", "content": {"type": "text", "text": output}}
                ],
            },
        },
    }


def _complete(req_id: int = 1, reason: str = "end_turn") -> dict:
    return {"id": req_id, "result": {"stopReason": reason}}


@pytest.mark.asyncio
async def test_scenario_plain_text_then_complete():
    c = _client_with_frames([_text("hello world"), _complete()])
    events = await _collect(c)
    assert [e.kind for e in events] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert events[0].text == "hello world"
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_scenario_thinking_then_text():
    c = _client_with_frames(
        [_text("pondering", "thinking"), _text("answer"), _complete()]
    )
    events = await _collect(c)
    kinds = [e.kind for e in events]
    assert kinds == [EVENT_THINKING_CHUNK, EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert c.last_prompt_stats.text_chunks == 1


@pytest.mark.asyncio
async def test_scenario_tool_call_then_update_result():
    c = _client_with_frames(
        [
            _tool_call("t1", "Read file"),
            _tool_update("t1", output="file contents"),
            _complete(),
        ]
    )
    events = await _collect(c)
    kinds = [e.kind for e in events]
    assert EVENT_TOOL_CALL in kinds
    assert EVENT_TOOL_RESULT in kinds
    assert kinds[-1] == EVENT_COMPLETE
    tc = next(e for e in events if e.kind == EVENT_TOOL_CALL)
    assert tc.title == "Read file" and tc.tool_call_id == "t1"
    tr = next(e for e in events if e.kind == EVENT_TOOL_RESULT)
    assert tr.tool_call_id == "t1" and "file contents" in tr.tool_output


@pytest.mark.asyncio
async def test_scenario_permission_request():
    c = _client_with_frames(
        [
            {
                "id": 55,
                "method": "session/request_permission",
                "params": {
                    "sessionId": _SID,
                    "toolCall": {"title": "Write", "toolCallId": "t9"},
                    "options": [],
                },
            },
            _complete(),
        ]
    )
    events = await _collect(c)
    perms = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(perms) == 1 and perms[0].title == "Write" and perms[0].request_id == 55
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_scenario_agent_switch():
    c = _client_with_frames(
        [
            {
                "method": METHOD_AGENT_SWITCHED,
                "params": {"sessionId": _SID, "agentName": "planner"},
            },
            _text("switched"),
            _complete(),
        ]
    )
    events = await _collect(c)
    sw = [e for e in events if e.kind == EVENT_AGENT_SWITCHED]
    assert sw and sw[0].text == "planner"


@pytest.mark.asyncio
async def test_scenario_tool_interrupted_marker_synthesizes_complete():
    c = _client_with_frames(
        [_text("Tool uses were interrupted, waiting for the next user prompt")]
    )
    c._emit_tool_interrupted_sel = MagicMock()
    events = await _collect(c)
    assert events[-1].kind == EVENT_COMPLETE
    assert any(e.kind == EVENT_TEXT_CHUNK for e in events)


@pytest.mark.asyncio
async def test_scenario_multi_text_chunks_stream_in_order():
    c = _client_with_frames([_text("one "), _text("two "), _text("three"), _complete()])
    events = await _collect(c)
    texts = [e.text for e in events if e.kind == EVENT_TEXT_CHUNK]
    assert texts == ["one ", "two ", "three"]
    assert c.last_prompt_stats.text_chunks == 3


@pytest.mark.asyncio
async def test_scenario_error_terminal_raises():
    from gideon.integrations.acp.errors import AcpError

    c = _client_with_frames(
        [_text("partial"), {"id": 1, "error": {"code": -32000, "message": "boom"}}]
    )
    with pytest.raises(AcpError):
        await _collect(c)


@pytest.mark.asyncio
async def test_scenario_command_result_formatted_as_text():
    c = _client_with_frames(
        [{"id": 1, "result": {"message": "usage report", "data": {"tokens": 42}}}]
    )
    events = [ev async for ev in c.stream_command("/usage")]
    texts = [e.text for e in events if e.kind == EVENT_TEXT_CHUNK]
    assert any("usage report" in t and '"tokens": 42' in t for t in texts)
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_scenario_jsonl_tool_results_surface(tmp_path):
    c = _client_with_frames([_text("running tool"), _complete()])
    c._session._session_files_dir = tmp_path
    (tmp_path / "sess-abc.jsonl").write_text(
        json.dumps(
            {
                "kind": "ToolResults",
                "data": {
                    "content": [
                        {
                            "kind": "toolResult",
                            "data": {
                                "toolUseId": "j1",
                                "content": [{"kind": "text", "data": "jsonl output"}],
                            },
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    events = await _collect(c)
    results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(results) == 1 and results[0].tool_call_id == "j1"
    assert "jsonl output" in results[0].tool_output


@pytest.mark.asyncio
async def test_scenario_send_message_concatenates_text():
    c = _client_with_frames([_text("Hello, "), _text("world!"), _complete()])
    result = await c.send_message("hi")
    assert result == "Hello, world!"
    assert c.last_prompt_stats.text_chunks == 2
    assert c._last_stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_scenario_send_message_excludes_thinking():
    c = _client_with_frames(
        [_text("deliberating", "thinking"), _text("final answer"), _complete()]
    )
    result = await c.send_message("hi")
    assert result == "final answer"
    assert c.last_prompt_stats.text_chunks == 1


@pytest.mark.asyncio
async def test_scenario_send_message_error_frame_raises():
    from gideon.integrations.acp.errors import AcpError

    c = _client_with_frames(
        [_text("partial"), {"id": 1, "error": {"code": -32000, "message": "kaboom"}}]
    )
    with pytest.raises(AcpError):
        await c.send_message("hi")


@pytest.mark.asyncio
async def test_scenario_failed_tool_surfaces_result():
    c = _client_with_frames(
        [
            _tool_call("t1", "Run ls"),
            _tool_update(
                "t1", status="failed", output="ls: /nope: No such file or directory"
            ),
            _complete(),
        ]
    )
    events = await _collect(c)
    tr = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(tr) == 1 and tr[0].tool_call_id == "t1"
    assert "No such file or directory" in tr[0].tool_output
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_scenario_stale_text_without_terminal_synthesizes_complete(monkeypatch):
    monkeypatch.setattr("gideon.integrations.acp.client._STALE_TURN_TIMEOUT", 0.1)
    c = _client_with_frames([_text("here is a partial reply")])
    events = await _collect(c)
    assert any(e.kind == EVENT_TEXT_CHUNK for e in events)
    assert events[-1].kind == EVENT_COMPLETE
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_scenario_stop_reason_passthrough_non_default():
    c = _client_with_frames([_text("cut short"), _complete(reason="max_tokens")])
    events = await _collect(c)
    assert events[-1].kind == EVENT_COMPLETE
    assert events[-1].stop_reason == "max_tokens"
    assert c._last_stop_reason == "max_tokens"
