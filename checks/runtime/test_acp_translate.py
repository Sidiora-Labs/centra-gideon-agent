"""Direct tests for the pure ACP↔neutral decoders in acp/translate.py.

These functions (text/tool/permission/command-result/JSONL decoding) are the single
translation surface shared by the N=1 AcpClient wrapper and the concurrent AcpSession.
Pre-P9#7 they were exercised only THROUGH the client's inline loop; this file tests them
directly so the coverage survives the client's slimming to a thin wrapper.
"""

from __future__ import annotations

import json

from gideon.integrations.acp import translate
from gideon.integrations.acp.types import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    JsonRpcMessage,
)


def _update(update: dict) -> JsonRpcMessage:
    return JsonRpcMessage(method="session/update", params={"update": update})


class TestExtractTextChunk:
    def test_plain_text(self):
        msg = _update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "hi"},
            }
        )
        text, is_thinking = translate.extract_text_chunk(msg)
        assert text == "hi" and is_thinking is False

    def test_thinking_flagged(self):
        msg = _update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "thinking", "text": "pondering"},
            }
        )
        text, is_thinking = translate.extract_text_chunk(msg)
        assert text == "pondering" and is_thinking is True

    def test_non_text_returns_none(self):
        msg = _update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "x"})
        text, _ = translate.extract_text_chunk(msg)
        assert text is None


class TestExtractToolEvent:
    def test_tool_call_event(self):
        msg = _update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "t1",
                "title": "Read file",
                "kind": "read",
                "rawInput": {"path": "/x"},
            }
        )
        ev = translate.extract_tool_event(msg, {}, {}, [])
        assert ev is not None and ev.kind == EVENT_TOOL_CALL
        assert ev.tool_call_id == "t1" and ev.title == "Read file"

    def test_non_tool_returns_none(self):
        msg = _update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "hi"},
            }
        )
        assert translate.extract_tool_event(msg, {}, {}, []) is None


class TestExtractToolUpdateEvents:
    def test_completed_yields_result(self):
        msg = _update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "t1",
                "status": "completed",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "done"}}
                ],
            }
        )
        events = translate.extract_tool_update_events(msg, {}, {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert len(results) == 1 and "done" in results[0].tool_output

    def test_failed_still_surfaces_result(self):
        msg = _update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "t1",
                "status": "failed",
                "content": [
                    {
                        "type": "content",
                        "content": {"type": "text", "text": "ls: /nope: No such file"},
                    }
                ],
            }
        )
        events = translate.extract_tool_update_events(msg, {}, {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert len(results) == 1 and "No such file" in results[0].tool_output


class TestBuildPermissionEvent:
    def test_permission_event_fields(self):
        from gideon.integrations.acp.dialect import DefaultDialect

        msg = JsonRpcMessage(
            id=55,
            method="session/request_permission",
            params={"toolCall": {"title": "Write", "toolCallId": "t9"}, "options": []},
        )
        offered: dict = {}
        ev = translate.build_permission_event(msg, DefaultDialect(), {}, {}, offered)
        assert ev.kind == EVENT_PERMISSION_REQUEST
        assert ev.title == "Write" and ev.request_id == 55

    def _seen_from_tool_call(
        self, *, title="Read file 'notes.md'", kind="read", call_id="c1"
    ):
        seen: dict = {}
        inputs: dict = {}
        translate.extract_tool_event(
            _update(
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": call_id,
                    "title": title,
                    "kind": kind,
                    "rawInput": {"path": "notes.md"},
                }
            ),
            inputs,
            seen,
            [],
        )
        return inputs, seen

    def _permission(self, tool_call: dict, inputs: dict, seen: dict, req="r1"):
        from gideon.integrations.acp.dialect import DefaultDialect

        msg = JsonRpcMessage(
            id=req,
            method="session/request_permission",
            params={"toolCall": tool_call, "options": []},
        )
        return translate.build_permission_event(msg, DefaultDialect(), inputs, seen, {})

    def test_titleless_permission_frame_is_named_from_the_preceding_tool_call(self):
        """G18 — the defect: a codex-shaped payload rendered `tool: "unknown"`."""
        inputs, seen = self._seen_from_tool_call()
        ev = self._permission(
            {"toolCallId": "c1", "kind": "read", "status": "pending"}, inputs, seen
        )
        assert (
            ev.title == "Read file 'notes.md'"
        ), "the card cannot name the tool it gates"

    def test_an_empty_title_is_an_absence_not_a_name(self):
        """`.get("title", "unknown")` only saw a MISSING key, so `title: ""` shipped a
        nameless card even when the correlation had the real name."""
        inputs, seen = self._seen_from_tool_call()
        ev = self._permission(
            {"toolCallId": "c1", "title": "", "status": "pending"}, inputs, seen
        )
        assert ev.title == "Read file 'notes.md'"

    def test_the_frames_own_title_wins_over_the_correlated_one(self):
        inputs, seen = self._seen_from_tool_call()
        ev = self._permission(
            {"toolCallId": "c1", "title": "Frame's own", "status": "pending"},
            inputs,
            seen,
        )
        assert ev.title == "Frame's own"

    def test_a_title_is_never_invented_when_no_frame_named_the_tool(self):
        """The vacuity floor for the fill: an uncorrelated id must stay `unknown` rather
        than borrow some other call's name."""
        inputs, seen = self._seen_from_tool_call()
        ev = self._permission(
            {"toolCallId": "no-such-call", "status": "pending"}, inputs, seen
        )
        assert ev.title == "unknown"

    def test_an_update_refines_one_field_without_erasing_the_other(self):
        """A `tool_call_update` that names the tool but omits `kind` must keep the kind the
        opening frame declared — the two fields are refined independently."""
        inputs, seen = self._seen_from_tool_call()
        translate.extract_tool_update_events(
            _update(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "c1",
                    "title": "Read file 'renamed.md'",
                }
            ),
            inputs,
            seen,
        )
        assert seen["c1"].title == "Read file 'renamed.md'"
        assert seen["c1"].kind == "read", "the update erased a kind it never declared"
        ev = self._permission({"toolCallId": "c1", "status": "pending"}, inputs, seen)
        assert ev.title == "Read file 'renamed.md'" and ev.tool_kind == "read"


class TestFormatCommandResult:
    def test_message_and_data(self):
        out = translate.format_command_result(
            {"message": "usage report", "data": {"tokens": 42}}
        )
        assert "usage report" in out and '"tokens": 42' in out


class TestInterruptedMarker:
    def test_exact_marker_matches(self):
        assert (
            translate.is_tool_interrupted_marker(translate.TOOL_INTERRUPTED_MARKER)
            is True
        )

    def test_prose_quoting_marker_does_not_match(self):
        assert (
            translate.is_tool_interrupted_marker(
                "I saw: " + translate.TOOL_INTERRUPTED_MARKER
            )
            is False
        )


class TestReadNewToolResults:
    def test_reads_tool_results_and_advances_pos(self, tmp_path):
        jsonl = tmp_path / "sess.jsonl"
        jsonl.write_text(
            json.dumps(
                {
                    "kind": "ToolResults",
                    "data": {
                        "content": [
                            {
                                "kind": "toolResult",
                                "data": {
                                    "toolUseId": "j1",
                                    "content": [
                                        {"kind": "text", "data": "jsonl output"}
                                    ],
                                },
                            }
                        ]
                    },
                }
            )
            + "\n"
        )
        events, pos = translate.read_new_tool_results(jsonl, 0)
        assert len(events) == 1 and events[0].tool_call_id == "j1"
        assert "jsonl output" in events[0].tool_output
        assert pos > 0
        events2, pos2 = translate.read_new_tool_results(jsonl, pos)
        assert events2 == [] and pos2 == pos

    def test_missing_file_is_noop(self, tmp_path):
        events, pos = translate.read_new_tool_results(tmp_path / "nope.jsonl", 0)
        assert events == [] and pos == 0


class TestCoerceToolContent:
    def test_flattens_text_blocks(self):
        out = translate.coerce_tool_content(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        assert "a" in out and "b" in out

    def test_plain_string_passthrough(self):
        assert translate.coerce_tool_content("plain") == "plain"


class TestEncodePromptContent:
    def test_wraps_message_as_content_blocks(self):
        blocks = translate.encode_prompt_content("hello")
        assert isinstance(blocks, list) and blocks
        assert any("hello" in json.dumps(b) for b in blocks)


def test_failure_decoder_uses_declared_keys_with_a_bounded_walk():
    assert translate.terminal_result_failed(
        {
            "status": "completed",
            "rawOutput": {"items": [{"Json": {"exit_status": "exit status: 3"}}]},
        }
    )
    assert translate.terminal_result_failed({"content": [{"isError": True}]})
    assert not translate.terminal_result_failed(
        {
            "rawOutput": {"exit_code": True, "text": "1 failed; exit 9"},
            "rawInput": {"exit_code": 3},
        }
    )
    cycle = {}
    cycle["self"] = cycle
    assert not translate._declares_failure(cycle)
    payload = {"exit_code": 1}
    for _ in range(translate._FAILURE_SCAN_MAX_DEPTH):
        payload = {"nested": payload}
    assert translate._declares_failure(payload)
    assert not translate._declares_failure({"nested": payload})


def test_tool_opening_and_refinement_preserve_declared_whole_file_changes():
    before, after = "old line\n", "new line\n"
    block = {"type": "diff", "path": "note.txt", "oldText": before, "newText": after}
    inputs, seen = {}, {}
    event = translate.extract_tool_event(
        _update(
            {"sessionUpdate": "tool_call", "toolCallId": "edit-1", "content": [block]}
        ),
        inputs,
        seen,
        [],
    )
    assert event.file_change == {"path": "note.txt", "before": before, "after": after}
    assert "-old line" in event.tool_input and "+new line" in event.tool_input
    update = translate.extract_tool_update_events(
        _update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "edit-1",
                "content": [block],
            }
        ),
        inputs,
        seen,
    )
    assert len(update) == 1 and update[0].file_change == event.file_change
    fragment = translate.extract_tool_event(
        _update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "edit-2",
                "rawInput": {
                    "command": "strReplace",
                    "path": "note.txt",
                    "oldStr": "old",
                    "newStr": "new",
                },
            }
        ),
        inputs,
        seen,
        [],
    )
    assert fragment.file_change is None and "-old" in fragment.tool_input


def test_journal_retains_partial_record_position_and_unicode(tmp_path):
    path = tmp_path / "events.jsonl"
    record = {
        "kind": "ToolResults",
        "data": {
            "content": [
                {
                    "kind": "toolResult",
                    "data": {
                        "toolUseId": "t",
                        "content": [{"kind": "text", "data": "café"}],
                    },
                }
            ]
        },
    }
    wire = json.dumps(record, ensure_ascii=False).encode()
    path.write_bytes(b"not json\n" + wire)
    events, position = translate.read_new_tool_results(path, 0)
    assert events == [] and position == len(b"not json\n")
    with path.open("ab") as stream:
        stream.write(b"\n")
    events, final = translate.read_new_tool_results(path, position)
    assert [event.tool_output for event in events] == ["café"]
    assert final == path.stat().st_size
    assert translate.read_new_tool_results(path, final) == ([], final)


def test_real_file_prompt_attachment_preserves_text_and_bytes(tmp_path):
    import base64

    path = tmp_path / "image.png"
    binary = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    path.write_bytes(binary)
    encoded = translate.encode_prompt_content("inspect " + str(path))
    assert encoded[0] == {"type": "text", "text": "inspect [image: image.png]"}
    assert encoded[1]["mimeType"] == "image/png"
    assert base64.b64decode(encoded[1]["data"]) == binary
