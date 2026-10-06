"""Real filesystem and FTS coverage for a large session catalog."""

import json
import time

from gideon.cognition.history import ConversationLog
from gideon.cognition.session_search import answer, get_indexer
from gideon.engine import session_search


def test_large_history_lists_from_metadata_and_reports_search_coverage(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    session_search.reset_for_tests()
    log = ConversationLog(tmp_path / "history")
    log.init()
    count = 12_005
    for position in range(count):
        key = f"chat-{position:05d}"
        metadata = {
            "_type": "metadata",
            "title": f"Conversation {position}",
            "message_count": 1,
            "memory_mode": "persistent",
        }
        content = "ordinary conversation"
        if position == 0:
            content = "needle archival result"
        elif position == count - 1:
            content = "needle newest result"
        elif position == count - 2:
            metadata["memory_mode"] = "incognito"
        elif position == count - 3:
            metadata["closed"] = True
        elif position == count - 4:
            metadata["created_by_app"] = "sample-app"
        path = log._path(key)
        path.write_text(
            json.dumps(metadata)
            + "\n"
            + json.dumps({"role": "user", "content": content})
            + "\n",
            encoding="utf-8",
        )

    first = log.list_sessions()
    listing_path = log._dir / "session_listing.json"
    listing_stamp = listing_path.stat().st_mtime_ns
    second = log.list_sessions()
    assert len(first) == count - 1
    assert len(second) == count - 1
    assert listing_path.stat().st_mtime_ns == listing_stamp
    cached = json.loads(listing_path.read_text(encoding="utf-8"))
    assert len(cached["entries"]) == count - 1
    assert "chat-12003.jsonl" not in cached["entries"]

    assert session_search.reindex_session(f"chat-{count - 1:05d}", log=log)
    indexer = get_indexer(log)
    try:
        partial = answer(log, "needle", limit=10)
        assert partial["complete"] is False
        assert partial["searched"]["chats"] < partial["searched"]["of"]
        assert any(row["key"] == f"chat-{count - 1:05d}" for row in partial["sessions"])

        fresh_key = "chat-dirty-write"
        log.append(fresh_key, "user", "prioritymark before the untouched backlog")
        deadline = time.monotonic() + 5
        while (
            fresh_key not in session_search.indexed_state(log)["keys"]
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert fresh_key in session_search.indexed_state(log)["keys"]
        fresh = answer(log, "prioritymark", limit=10)
        assert any(row["key"] == fresh_key for row in fresh["sessions"])

        complete = answer(log, "needle", limit=10, rest=True)
        assert complete["complete"] is True
        assert complete["searched"]["chats"] == complete["searched"]["of"]
        assert {row["key"] for row in complete["sessions"]} >= {
            f"chat-{count - 1:05d}",
            "chat-00000",
        }

        app_scoped = answer(
            log, "needle", limit=10, rest=True, request_app="sample-app"
        )
        assert app_scoped["searched"]["of"] == 1
        assert app_scoped["complete"] is True
    finally:
        indexer.stop(wait=True)
        session_search.reset_for_tests()
