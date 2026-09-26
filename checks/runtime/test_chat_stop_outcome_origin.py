"""The stored stop record is the source for the chat turn's final outcome."""

import json

import pytest

from gideon.interfaces.dashboard.chat_handlers import _resolve_stop_event
from gideon.interfaces.dashboard.chat_utils import _prepare_messages
from gideon.interfaces.dashboard.state import _ChatSession


@pytest.mark.parametrize(
    "outcome,state", [("soft", "stopped"), ("hard", "stop_failed_reset")]
)
def test_resolved_stop_record_reaches_session_detail(outcome, state):
    session = _ChatSession("source-stop")
    session.append("user", "Answer briefly", "msg msg-u")
    session.append("assistant", "Partial answer", "msg msg-a")
    stop = {
        "kind": "stop_event", "id": "stop-source-1", "state": "stopping",
        "outcome": None, "ts_start": "2026-09-26T00:00:00+00:00",
    }
    session._stop_event_id = stop["id"]
    serialized = json.dumps(stop)
    session.append("system", serialized, serialized)

    _resolve_stop_event(session, outcome)
    detail = _prepare_messages(session.messages, running=False)
    assert [row["role"] for row in detail] == ["user", "assistant", "system"]
    record = detail[-1]
    assert record["meta"]["kind"] == "stop_event"
    assert record["meta"]["id"] == stop["id"]
    assert record["meta"]["state"] == state
    assert record["meta"]["outcome"] == outcome
    assert json.loads(record["content"]) == record["meta"]
