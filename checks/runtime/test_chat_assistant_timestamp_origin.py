"""Assistant history timestamps come from the persisted gateway message."""

from datetime import datetime

from gideon.interfaces.dashboard.chat_utils import _prepare_messages
from gideon.interfaces.dashboard.state import _ChatSession


def test_assistant_timestamp_survives_session_detail_preparation():
    session = _ChatSession("timestamp-source")
    session.append("user", "Summarize", "msg msg-u")
    session.append("assistant", "Recorded answer", "msg msg-a")

    stored = session.messages[-1]
    detail = _prepare_messages(session.messages, running=False)
    assistant = detail[-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Recorded answer"
    assert assistant["ts"] == stored["ts"]
    assert datetime.fromisoformat(assistant["ts"])
