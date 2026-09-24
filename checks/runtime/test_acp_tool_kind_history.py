from pathlib import Path

import pytest

from gideon.cognition.history import ConversationLog
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_runner import (
    _capture_declared_file_change,
    _flush_file_changes,
)
from gideon.interfaces.dashboard.chat_utils import (
    _append_tool_row,
    persisted_history_key,
)
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.mark.parametrize("kind", ["read", "edit", "execute", ""])
@pytest.mark.parametrize("tool_call_id", ["acp-tool-1", ""])
def test_tool_kind_survives_real_history_serialization(tmp_path, kind, tool_call_id):
    log = ConversationLog(base_dir=tmp_path / "history")
    state = ConsoleState(ConversationDirectory(AppConfig()), 0, conversation_log=log)
    session = _ChatSession("dashboard:acp-kind")
    session.acp_provider = "codex"
    _append_tool_row(
        session,
        "Inspect workspace",
        tool_call_id=tool_call_id,
        kind=kind,
        purpose="inspect",
        input_preview='{"path":"src/main.py"}',
    )
    save_session_to_history(state, session)
    rows = log.read_messages(persisted_history_key(log, session.key))
    assert len(rows) == 1
    assert rows[0]["role"] == "tool"
    meta = rows[0].get("meta", {})
    if kind:
        assert meta["kind"] == kind
    else:
        assert "kind" not in meta
    if tool_call_id:
        assert meta["tool_call_id"] == tool_call_id
        assert meta["purpose"] == "inspect"
        assert meta["input"] == '{"path":"src/main.py"}'


def test_diff_chips_use_declared_changes_without_a_tool_kind():
    session = _ChatSession("dashboard:acp-diff")
    _append_tool_row(
        session, "Edit", tool_call_id="edit-1", kind="", purpose="", input_preview=""
    )
    change = {"path": "src/main.py", "before": "old", "after": "new"}
    _capture_declared_file_change(session, change)
    session.append("assistant", "Updated file")
    _flush_file_changes(session)
    assert "kind" not in session.messages[0]["meta"]
    assert session.messages[1]["meta"]["file_changes"] == [change]
    doc = (
        Path(__file__).resolve().parents[2] / "docs/agents/acp-parity.md"
    ).read_text()
    assert "Diff chips do not depend on `tool_kind`" in doc
    assert "tool-row `meta.kind`" in doc
