from __future__ import annotations

import json

from gideon.cognition.history import ConversationLog
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import state_history
from gideon.workspace import snapshot


def test_real_session_listing_sidecar_is_derived_inside_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    sessions = tmp_path / "sessions"
    log = ConversationLog(base_dir=sessions)
    log.append("durability-vector", "user", "A durable session entry")
    assert [row["key"] for row in log.list_sessions()] == ["durability-vector"]

    listing = sessions / "session_listing.json"
    summary = log.write_summary(
        "durability-vector",
        summary="A real local summary",
        summarized=1,
        reduced=1,
    )
    summary_path = log.summary_path("durability-vector")
    assert summary_path.is_file() and summary["summary"] == "A real local summary"
    payload = json.loads(listing.read_text(encoding="utf-8"))
    assert payload["version"] == 1 and payload["root"] == str(sessions.resolve())
    assert "durability-vector.jsonl" in payload["entries"]

    entry = inv.by_id("sessions")
    assert entry is not None and entry.path == "sessions"
    assert entry.derived_within == ("*.summary.json", "session_listing.json")
    ignored = snapshot._derived_ignore(entry.path, sessions)(
        str(sessions), [listing.name, summary_path.name, log._path("durability-vector").name]
    )
    assert ignored == {listing.name, summary_path.name}

    # The actual time-travel recorder has no sessions root, so this cache needs
    # no additional state-history deny pattern.
    assert state_history.root_for_path(listing, home=tmp_path) is None
