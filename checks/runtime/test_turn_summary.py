from gideon.interfaces.dashboard.chat_runner import _persist_turn_summary
from gideon.interfaces.dashboard.state import _ChatSession


def test_completed_outcome_is_extracted_redacted_and_attached_to_last_assistant():
    session = _ChatSession("summary")
    session.append("assistant", "earlier")
    session.append("tool", "Read")
    session.append("assistant", "final")

    _persist_turn_summary(
        session,
        "## Completed deploy with token ghp_abcdefghijklmnopqrstuvwxyz123456\nMore detail",
    )

    assert "ghp_" not in session.messages[-1]["meta"]["summary"]
    assert session.messages[0].get("meta") is None


def test_turn_without_outcome_persists_no_summary():
    session = _ChatSession("summary")
    session.append("assistant", "tool preamble")

    _persist_turn_summary(session, " \n")

    assert session.messages[-1].get("meta") is None
