"""Volatile runtime notes follow user/tool blocks without changing the stable prefix."""

from __future__ import annotations

import pytest

from gideon.integrations.llm.anthropic import _VOLATILE_MESSAGE_KEY, _translate_messages


def test_untagged_list_is_byte_identical_to_pre_pcs1_behavior():
    """A plain system + user + assistant list → exactly the pre-PCS-1 output.

    The expected ``(system, messages)`` is constructed by hand from the original
    logic: system content concatenated into ``system=``; plain user/assistant
    messages pass through as ``{role, content}``. No ``_volatile`` key anywhere.
    """
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]

    system, out = _translate_messages(messages)

    assert system == "You are a helpful assistant."
    assert out == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_untagged_multi_system_concatenation_unchanged():
    """Two untagged system messages still join with the historical ``\\n\\n`` separator."""
    messages = [
        {"role": "system", "content": "line one"},
        {"role": "system", "content": "line two"},
        {"role": "user", "content": "go"},
    ]

    system, out = _translate_messages(messages)

    assert system == "line one\n\nline two"
    assert out == [{"role": "user", "content": "go"}]


def test_untagged_tool_and_toolcall_shapes_unchanged():
    """A tool-call / tool-result round trip is untouched by the volatile routing."""
    messages = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "echo", "arguments": '{"x": "hi"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "OUT:hi"},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out == [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "echo",
                    "input": {"x": "hi"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "OUT:hi"}
            ],
        },
    ]


def test_volatile_note_routes_to_tail_not_system():
    """A ``_volatile`` system note is NOT hoisted; it is the LAST returned message."""
    messages = [
        {"role": "system", "content": "STABLE base prompt"},
        {"role": "user", "content": "the assembled context"},
        {
            "role": "system",
            "content": "[tool catalog] VOLATILE per-turn note",
            _VOLATILE_MESSAGE_KEY: True,
        },
    ]

    system, out = _translate_messages(messages)

    assert system == "STABLE base prompt"
    assert "VOLATILE per-turn note" not in system
    assert len(out) == 1
    assert out[-1]["content"] == [
        {"type": "text", "text": "the assembled context"},
        {"type": "text", "text": "[tool catalog] VOLATILE per-turn note"},
    ]
    assert _VOLATILE_MESSAGE_KEY not in out[-1]


def test_multiple_volatile_notes_each_ship_once_in_order():
    """If several volatile notes appear, each ships exactly once, in original order, at the tail."""
    messages = [
        {"role": "user", "content": "context"},
        {"role": "system", "content": "note A", _VOLATILE_MESSAGE_KEY: True},
        {"role": "system", "content": "note B", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out == [{"role": "user", "content": [
        {"type": "text", "text": "context"},
        {"type": "text", "text": "note A"},
        {"type": "text", "text": "note B"},
    ]}]


def test_content_equivalence_note_relocated_not_lost_or_duplicated():
    """Every input message's content appears exactly once across ``(system, messages)``."""
    messages = [
        {"role": "system", "content": "STABLE"},
        {"role": "user", "content": "USERCTX"},
        {"role": "assistant", "content": "PRIORREPLY"},
        {"role": "system", "content": "VOLATILE", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    haystack = system + " " + " ".join(str(m.get("content", "")) for m in out)
    for token in ("STABLE", "USERCTX", "PRIORREPLY", "VOLATILE"):
        assert (
            haystack.count(token) == 1
        ), f"{token!r} must appear exactly once, not dropped/duplicated"


def test_native_shape_stable_context_leads_volatile_at_tail():
    """Native-shaped list (user assembled-context + volatile turn_note).

    There is no stable base system message in the native loop, so ``system=`` is
    empty; the stable assembled context (the user message) leads at ``messages[0]``
    and the volatile note sits at the tail — exactly the reordering F1 requires.
    """
    messages = [
        {"role": "user", "content": "ASSEMBLED CONTEXT (stable across the turn)"},
        {
            "role": "system",
            "content": "[tool catalog] volatile",
            _VOLATILE_MESSAGE_KEY: True,
        },
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert len(out) == 1
    assert out[0]["content"] == [
        {"type": "text", "text": "ASSEMBLED CONTEXT (stable across the turn)"},
        {"type": "text", "text": "[tool catalog] volatile"},
    ]


def test_stable_system_leads_when_present():
    """With a stable base system + assembled context, system= carries the stable prefix."""
    messages = [
        {"role": "system", "content": "STABLE PREFIX"},
        {"role": "user", "content": "ASSEMBLED CONTEXT"},
        {"role": "system", "content": "volatile", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == "STABLE PREFIX"
    assert len(out) == 1
    assert out[0]["content"] == [
        {"type": "text", "text": "ASSEMBLED CONTEXT"},
        {"type": "text", "text": "volatile"},
    ]


def test_v1_catalog_still_reaches_the_model_just_late():
    """V1 structural: the tool catalog (in the turn_note) is still in the final wire payload.

    A full-live-model recency check (does the model still call ``tool_schema`` after the
    catalog moved late?) is an owner-validation step, not a unit test. The structural
    property it rests on is: the catalog is delivered — present in ``messages`` — not
    dropped. It just no longer leads.
    """
    catalog_note = (
        '[tool catalog] call tool_schema("name") to expand; nothing is disabled.'
    )
    messages = [
        {"role": "user", "content": "assembled context"},
        {"role": "system", "content": catalog_note, _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    payload_text = "\n".join(str(m.get("content", "")) for m in out)
    assert catalog_note in payload_text
    assert catalog_note not in system


def test_parallel_tool_result_merge_does_not_mutate_existing_caller_blocks():
    import copy

    from gideon.integrations.llm.anthropic import _translate_messages

    messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": "one"}],
        },
        {"role": "tool", "tool_call_id": "b", "content": "two"},
    ]
    before = copy.deepcopy(messages)
    system, translated = _translate_messages(messages)
    assert system == ""
    assert messages == before
    assert [block["tool_use_id"] for block in translated[0]["content"]] == ["a", "b"]
    assert translated[0]["content"] is not messages[0]["content"]
