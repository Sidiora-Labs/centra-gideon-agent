import json

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.session_map import TURN_TELEMETRY_KEY, stamp_turn_telemetry
from gideon.cognition.history import ConversationLog
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_utils import persisted_history_key
from gideon.interfaces.dashboard.chat_runner import _turn_complete_line
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def test_live_turn_line_is_saved_once_and_survives_a_reload(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIDEON_HOME", raising=False)
    log = ConversationLog(base_dir=tmp_path / "history")
    state = ConsoleState(ConversationDirectory(AppConfig()), 0.0, conversation_log=log)
    session = _ChatSession("telemetry-reload")
    session.append("user", "What changed?")
    session.append(
        "assistant",
        "Two files changed.",
        meta={"last_turn_outcome": "complete", "image_delivery": {"diagram.png": "pixels"}},
    )
    line = _turn_complete_line(
        events=3,
        tool_calls=1,
        context_pct=42,
        input_tokens=1200,
        output_tokens=340,
        cost_usd=0.0123,
        priced=True,
    )

    assert stamp_turn_telemetry(session.messages, line)
    assert not stamp_turn_telemetry(session.messages, "replacement line")
    save_session_to_history(state, session, force=True)

    history_key = persisted_history_key(log, session.key)
    reloaded = ConversationLog(base_dir=tmp_path / "history").read_messages(history_key)
    assistant = [message for message in reloaded if message.get("role") == "assistant"][-1]
    assert assistant["meta"][TURN_TELEMETRY_KEY]["line"] == line
    assert assistant["meta"]["last_turn_outcome"] == "complete"
    assert assistant["meta"]["image_delivery"] == {"diagram.png": "pixels"}
    assert json.loads(log._path(history_key).read_text(encoding="utf-8").splitlines()[-1])["meta"][TURN_TELEMETRY_KEY]["line"] == line


def test_telemetry_only_stamps_the_assistant_after_the_latest_user():
    messages = [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Earlier reply", "meta": {TURN_TELEMETRY_KEY: {"line": "old line"}}},
        {"role": "user", "content": "Current"},
        {"role": "assistant", "content": "Current reply"},
    ]

    assert stamp_turn_telemetry(messages, "Turn complete: 1 events, 0 tool calls")
    assert messages[1]["meta"][TURN_TELEMETRY_KEY]["line"] == "old line"
    assert messages[3]["meta"][TURN_TELEMETRY_KEY]["line"] == "Turn complete: 1 events, 0 tool calls"
