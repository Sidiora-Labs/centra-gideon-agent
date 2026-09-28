"""Derived model summaries keep the authoritative chat available in full."""

from __future__ import annotations

import json

from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import ConversationLog


def _big_session(
    log: ConversationLog, key: str, *, topics: int = 4, per_topic: int = 6
):
    for topic in range(topics):
        for index in range(per_topic):
            log.append(key, "user", f"topic {topic} question {index} " + ("x" * 300))
            log.append(key, "assistant", f"topic {topic} answer {index} " + ("y" * 300))


def test_summary_changes_only_the_model_view_and_invalidates_on_edit(tmp_path):
    log = ConversationLog(base_dir=tmp_path / "sessions")
    key = "s1"
    _big_session(log, key)
    transcript_path = log._path(key)
    before = transcript_path.read_bytes()
    messages = log.read_messages(key)
    assert len(messages) == 48

    log.write_summary(
        key,
        summary="The first eight messages cover early planning questions.",
        summarized=8,
        reduced=16,
        messages=messages,
    )

    view = log.model_view(key)
    assert view[0] == {
        "role": "summary",
        "content": "The first eight messages cover early planning questions.",
    }
    assert messages[0]["content"] not in [message["content"] for message in view]
    assert transcript_path.read_bytes() == before
    assert len(log.read_messages(key)) == 48
    assert not (log._dir / "archive").exists()

    log.append(key, "user", "new tail message")
    tail_view = log.model_view(key)
    assert [message["content"] for message in tail_view].count("new tail message") == 1
    assert tail_view[0]["role"] == "summary"

    lines = transcript_path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[2])
    edited["content"] = "edited covered message"
    lines[2] = json.dumps(edited)
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log._invalidate_cache(key)

    assert log.read_summary(key) is None
    changed_view = log.model_view(key)
    assert changed_view[0]["role"] == "user"
    assert "edited covered message" in [message["content"] for message in changed_view]


def test_explicit_empty_prior_transcript_does_not_read_saved_history(tmp_path):
    log = ConversationLog(base_dir=tmp_path / "sessions")
    _big_session(log, "s1")
    builder = PromptAssembler(conversation_log=log)

    assert builder._thread_block("s1", False, None, prior_transcript=[]) == ""
