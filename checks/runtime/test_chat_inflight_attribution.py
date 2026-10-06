from gideon.interfaces.dashboard.chat_persistence import (
    in_flight_index,
    tag_turn_message,
)
from gideon.interfaces.dashboard.chat_runner import _flush_segment
from gideon.interfaces.dashboard.state import _ChatSession


def test_skill_and_tool_records_stay_with_their_initiating_turn():
    session = _ChatSession("turn-attribution")
    session.append("user", "repeat this request", "msg msg-u", ts="old-turn")
    session.append("assistant", "Earlier answer", "msg msg-a")
    session.append(
        "inject", "repeat this request", "msg msg-inject", ts="automation-turn"
    )

    origin_index = in_flight_index(session, "repeat this request")
    assert origin_index == 2
    origin = session.messages[origin_index]
    turn_id = origin["ts"]
    tag_turn_message(origin, turn_id)
    skills = [{"name": "incident-review", "state": "admitted", "loaded_tokens": 128}]
    origin["meta"]["skills_used"] = skills

    session.append(
        "tool",
        "search incidents",
        "msg msg-tool",
        meta={"tool_call_id": "request-7"},
    )
    tool_row = session.messages[-1]
    tag_turn_message(tool_row, turn_id)
    session._skills_used = skills
    _flush_segment(
        None, session, "The incident record is ready.", broadcast=False, turn_id=turn_id
    )

    assert origin["meta"]["turn_id"] == "automation-turn"
    assert origin["meta"]["skills_used"] == skills
    assert tool_row["meta"]["turn_id"] == "automation-turn"
    assert session.messages[-1]["meta"]["turn_id"] == "automation-turn"
    assert session.messages[-1]["meta"]["skills_used"] == skills

    session.append("user", "a later request", "msg msg-u", ts="later-turn")
    later_index = in_flight_index(session, "a later request")
    assert later_index == len(session.messages) - 1
    assert "skills_used" not in session.messages[later_index].get("meta", {})
    assert session.messages[later_index].get("meta", {}).get("turn_id") != turn_id
