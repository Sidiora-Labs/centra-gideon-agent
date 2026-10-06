from gideon.engine.rooms.store import RoomStore
from gideon.integrations.llm.events import EVENT_COMPLETE, AgentEvent
from gideon.operations import usage_ledger


def test_room_transcript_and_usage_keep_notice_before_actual_responder(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    rooms = RoomStore(tmp_path)
    room = rooms.create(
        "Research",
        [{"id": "analyst", "agent": "Analyst", "name": "Analyst"}],
    )
    turn_id = "fallback-turn"

    rooms.append(
        room.id,
        "system",
        "Analyst ran on backup:large instead of preferred:small.",
        speaker="system",
        turn_id=turn_id,
    )
    rooms.append(
        room.id,
        "assistant",
        "The evidence supports the proposal.",
        speaker="analyst",
        turn_id=turn_id,
    )
    usage_ledger.record_from_event(
        AgentEvent(
            kind=EVENT_COMPLETE,
            input_tokens=44,
            output_tokens=18,
            served_model_ref="backup:large",
        ),
        source="room",
        session_key="room:research:analyst",
        agent="Analyst",
        provider="native",
        model="preferred:small",
        estimate_if_missing=False,
    )

    transcript = rooms.messages(room.id)
    assert [message["role"] for message in transcript[-2:]] == ["system", "assistant"]
    assert transcript[-2]["turn_id"] == turn_id
    row = usage_ledger._iter_rows()[0]
    assert row["source"] == "room"
    assert row["agent"] == "Analyst"
    assert row["provider"] == "backup"
    assert row["model"] == "large"
