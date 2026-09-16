"""chat-task-modes — the task-mode tool gate + framing (docs/plans/ports/chat-task-modes.md).

Task mode (agent/ask/plan/build) is an axis ORTHOGONAL to the approval mode: it gates
*which* tools run + how the agent frames the work. These cover the pure gate logic
(security-critical: Ask must block every mutation) + the framing selection.
"""

from __future__ import annotations

import pytest

from gideon.interfaces.dashboard.chat_utils import task_mode_denies, task_mode_framing


class _S:
    def __init__(self, mode: str) -> None:
        self._task_mode = mode


_CASES = [
    ("agent", "write_file", "edit", "{}", False),
    ("agent", "bash", "command", '{"command":"rm -rf x"}', False),
    ("plan", "read_file", "read", "{}", False),
    ("plan", "grep", "read", "{}", False),
    ("plan", "bash", "command", '{"command":"ls -la"}', False),
    ("plan", "write_file", "edit", "{}", True),
    ("plan", "bash", "command", '{"command":"rm -rf x"}', True),
    ("plan", "artifact_save", "", "{}", True),
    ("ask", "read_file", "read", "{}", False),
    ("ask", "grep", "read", "{}", False),
    ("ask", "bash", "command", '{"command":"ls -la"}', False),
    ("ask", "bash", "command", '{"command":"rm -rf x"}', True),
    ("ask", "bash", "command", '{"command":"cat a > b"}', True),
    ("ask", "write_file", "edit", "{}", True),
    ("ask", "artifact_save", "", "{}", True),
    ("ask", "memory_recall", "", "{}", False),
    ("ask", "delete_thing", "delete", "{}", True),
    ("ask", "subagent_run", "", "{}", True),
    ("build", "read_file", "read", "{}", False),
    ("build", "artifact_save", "", "{}", False),
    ("build", "widget_create", "", "{}", False),
    ("build", "skill_invoke", "", "{}", False),
    ("build", "bash", "command", '{"command":"rm -rf x"}', True),
    ("build", "write_file", "edit", "{}", True),
    ("build", "delete_artifact", "", "{}", True),
    (
        "build",
        "remove_widget",
        "",
        "{}",
        True,
    ),
    ("ask", "delete_artifact", "", "{}", True),
    (
        "ask",
        "image_generate",
        "",
        '{"prompt":"a cat"}',
        True,
    ),
    ("plan", "image_generate", "", '{"prompt":"a cat"}', True),
    (
        "build",
        "image_generate",
        "",
        '{"prompt":"a cat"}',
        False,
    ),
    ("agent", "image_generate", "", '{"prompt":"a cat"}', False),
    (
        "ask",
        "prompt_render",
        "read",
        "{}",
        False,
    ),
]


@pytest.mark.parametrize("mode,title,kind,inp,want_deny", _CASES)
def test_task_mode_gate(mode, title, kind, inp, want_deny):
    denied = bool(task_mode_denies(_S(mode), title, kind, inp))
    assert (
        denied is want_deny
    ), f"[{mode}] {title}/{kind}: got deny={denied} want={want_deny}"


def test_agent_mode_never_denies():
    s = _S("agent")
    for title, kind in [
        ("anything", "edit"),
        ("bash", "command"),
        ("delete_all", "delete"),
    ]:
        assert task_mode_denies(s, title, kind, "{}") == ""


def test_framing_per_mode():
    for mode in ("agent", "ask", "plan", "build"):
        f = task_mode_framing(_S(mode))
        assert f and mode.capitalize() in f
    agent_f = task_mode_framing(_S("agent")).lower()
    assert "lifted" in agent_f or "full execution" in agent_f
    for mode in ("ask", "plan", "build"):
        assert "SWITCH_TO_AGENT" in task_mode_framing(_S(mode))
    assert "SWITCH_TO_AGENT" not in task_mode_framing(_S("agent"))


def test_framing_unknown_mode_is_empty():
    assert task_mode_framing(_S("nonsense")) == ""


def test_framing_layers_on_default_system_prompt_not_replaces(tmp_path):
    """S05 C6 regression: framing threaded as system_prompt_suffix must LAYER on
    the resolved default-agent prompt — folding it into system_prompt_override
    (the old wiring) made the 4-line posture block the ENTIRE system prompt,
    silently dropping identity ({{bot_name}}), widgets, and safety rules."""
    from gideon.cognition.context import PromptAssembler

    cb = PromptAssembler()
    out, _ = cb.build_message(
        "hello",
        True,
        session_key="dashboard:tm-layer-test",
        agent="gideon",
        system_prompt_suffix=task_mode_framing(_S("agent")),
    )
    assert "You are " in out
    assert "Task mode: Agent" in out
    out2, _ = cb.build_message(
        "hello",
        True,
        session_key="dashboard:tm-layer-test2",
        agent="gideon",
        system_prompt_override="You are TestBot, a custom persona.",
        system_prompt_suffix=task_mode_framing(_S("ask")),
    )
    assert "TestBot" in out2 and "Task mode: Ask" in out2
