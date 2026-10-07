"""Persist and reopen canonical conversation records for the console contract."""

import json
import os
from pathlib import Path

from gideon.cognition.history import ConversationLog


def main():
    home = Path(os.environ["GIDEON_HOME"])
    journal = home / "sessions"
    key = "dashboard:marker-history"
    rows = [
        ("user", "Inspect saved history", None),
        ("assistant", "First response", None),
        ("assistant", "Second response", None),
        ("user", "Retry safely", None),
        (
            "permission",
            "Terminal",
            {"approval_id": "approval-1", "tool": "Terminal", "risk": "destructive"},
        ),
        ("tool", "Terminal", {"tool_call_id": "tool-1", "done": True, "ok": False}),
        ("error", "The command failed", None),
        ("assistant", "Stopped safely", None),
    ]
    store = ConversationLog(journal)
    for role, content, meta in rows:
        store.append(key, role, content, meta=meta)
    reopened = ConversationLog(journal)
    messages = reopened.read_messages_chained(key)
    paths = list(journal.glob("*.jsonl"))
    if len(paths) != 1 or len(messages) != len(rows):
        raise RuntimeError("Canonical conversation history did not persist completely")
    print(
        json.dumps({"messages": messages, "journal": str(paths[0].relative_to(home))})
    )


if __name__ == "__main__":
    main()
