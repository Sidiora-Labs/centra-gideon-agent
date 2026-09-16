import json
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.cognition import history
from gideon.cognition.history import ConversationLog
from gideon.engine import session_restrictions, session_search


@pytest.fixture
def journal_home(tmp_path, monkeypatch):
    home = tmp_path / "journal-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_journal_cache_observes_file_stamps_and_preserves_shared_records(tmp_path):
    journal = ConversationLog(tmp_path)
    journal.append(
        "conversation:one", "user", "first", agent="operator", tab_id="tab-one"
    )
    records = journal.read_messages("conversation:one")
    metadata = journal.get_metadata("conversation:one")
    assert journal.read_messages("conversation:one") is records
    assert journal.get_metadata("conversation:one") is metadata
    records[0]["content"] = "cached edit"
    assert journal.recent("conversation:one")[0]["content"] == "cached edit"
    assert _lines(journal._path("conversation:one"))[1]["content"] == "first"
    path = journal._path("conversation:one")
    stamp = path.stat().st_mtime
    with path.open("a") as stream:
        stream.write(
            json.dumps({"role": "assistant", "content": "external", "ts": "later"})
            + "\n"
        )
    os.utime(path, (stamp + 2, stamp + 2))
    assert [row["content"] for row in journal.read_messages("conversation:one")] == [
        "first",
        "external",
    ]
    journal.update_metadata("conversation:one", {"title": "Renamed"})
    assert journal.get_metadata("conversation:one")["title"] == "Renamed"
    path.unlink()
    assert journal.read_messages("conversation:one") == []
    assert journal.get_metadata("conversation:one") == {}
    assert "conversation:one" not in journal._msg_cache
    assert "conversation:one" not in journal._meta_cache


def test_existing_journal_supports_independent_concurrent_appends(tmp_path):
    journal = ConversationLog(tmp_path)
    journal.append("parallel", "system", "seed")

    def append_series(worker):
        for index in range(25):
            journal.append("parallel", "user", f"{worker}:{index}")

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(append_series, range(8)))
    records = journal.read_messages("parallel")
    assert len(records) == 201
    assert {row["content"] for row in records[1:]} == {
        f"{worker}:{index}" for worker in range(8) for index in range(25)
    }
    assert (
        sum(row.get("_type") == "metadata" for row in _lines(journal._path("parallel")))
        == 1
    )


def test_archive_exclusive_creation_under_real_concurrency(tmp_path):
    def save(index):
        return history._archive_lines(
            "channel:thread.1", [json.dumps({"index": index}) + "\n"], "owned", tmp_path
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        paths = list(workers.map(save, range(24)))
    assert len(set(paths)) == 24
    assert {_lines(path)[1]["index"] for path in paths} == set(range(24))
    assert all(_lines(path)[0]["count"] == 1 for path in paths)
    assert all(path.name.startswith("channel_thread.1__") for path in paths)


def test_rewrite_archives_corruption_and_only_removed_records(tmp_path):
    journal = ConversationLog(tmp_path)
    journal.append("owned", "user", "keep", agent="special", tab_id="tab")
    journal.append("owned", "assistant", "drop")
    journal.update_metadata(
        "owned", {"title": "Title", "memory_mode": "temporary", "custom": "discarded"}
    )
    journal.mark_consolidated("owned", 8)
    before = journal.get_metadata("owned")
    kept = dict(reversed(list(journal.read_messages("owned")[0].items())))
    with journal._path("owned").open("a") as stream:
        stream.write("broken-record\n\n")
    journal.rewrite_session("owned", [kept], reason="owned-compress")
    assert journal.read_messages("owned") == [kept]
    metadata = journal.get_metadata("owned")
    assert metadata["created_at"] == before["created_at"]
    assert metadata["last_consolidated"] == 8 and metadata["memory_mode"] == "temporary"
    assert not {"agent", "tab_id", "title", "custom"}.intersection(metadata)
    assert journal.get_unconsolidated("owned") == ([], 1)
    assert journal.unconsolidated_count("owned") == 0
    archive = next((tmp_path / "archive").glob("owned__*.jsonl"))
    raw = archive.read_text()
    assert json.loads(raw.splitlines()[0])["reason"] == "owned-compress"
    assert json.loads(raw.splitlines()[0])["count"] == 2
    assert "broken-record" in raw and '"content": "drop"' in raw
    assert '"content": "keep"' not in raw


def test_rotation_archives_exact_prefix_and_resets_offsets(tmp_path, monkeypatch):
    journal = ConversationLog(tmp_path)
    for number in range(5):
        journal.append("owned", "user", str(number))
    journal.mark_consolidated("owned", 4)
    metadata = journal.get_metadata("owned")
    monkeypatch.setattr(history, "_SESSION_KEEP_LINES", 2)
    monkeypatch.setattr(history, "_SESSION_MAX_BYTES", 1)
    journal._maybe_rotate(journal._path("owned"))
    assert [row["content"] for row in journal.read_messages("owned")] == ["3", "4"]
    assert journal.get_metadata("owned")["last_consolidated"] == 0
    assert journal.get_metadata("owned")["created_at"] == metadata["created_at"]
    archive = next((tmp_path / "archive").glob("owned__*.jsonl"))
    assert [row["content"] for row in _lines(archive)[1:]] == ["0", "1", "2"]
    assert _lines(archive)[0]["reason"] == "rotate"


def test_metadata_updates_follow_existing_header_contract(tmp_path):
    journal = ConversationLog(tmp_path)
    journal.update_metadata("absent", {"title": "no create"})
    journal.mark_consolidated("absent", 3)
    assert not journal.has_session("absent")
    path = journal._path("corrupt")
    path.write_text("broken\n")
    journal.update_metadata("corrupt", {"title": "ignored"})
    assert path.read_text() == "broken\n"
    with pytest.raises(json.JSONDecodeError):
        journal.mark_consolidated("corrupt", 2)
    path.write_text(json.dumps({"role": "user", "content": "old"}) + "\n")
    journal.update_metadata("corrupt", {"title": "ignored"})
    assert "title" not in _lines(path)[0]
    journal.mark_consolidated("corrupt", 2)
    assert _lines(path)[0]["last_consolidated"] == 2


def test_lineage_groups_dashboard_files_and_rebuilds_after_explicit_invalidation(
    tmp_path, journal_home
):
    journal = ConversationLog(tmp_path / "sessions")
    journal.append("dashboard:chat-2-b", "user", "second", tab_id="shared")
    journal.append("dashboard:chat-1-a", "user", "first", tab_id="shared")
    journal.append("dashboard:chat-3-c", "user", "other", tab_id="different")
    assert [
        row["content"] for row in journal.read_messages_chained("dashboard:chat-2-b")
    ] == ["first", "second"]
    journal.append("dashboard:chat-4-d", "assistant", "third", tab_id="shared")
    journal.invalidate_tab_id_cache()
    assert [
        row["content"] for row in journal.read_messages_chained("dashboard:chat-4-d")
    ] == ["first", "second", "third"]
    assert journal.delete_session("dashboard:chat-1-a")
    assert [
        row["content"] for row in journal.read_messages_chained("dashboard:chat-2-b")
    ] == ["second", "third"]
    journal.append("channel:legacy", "user", "legacy")
    assert journal.read_messages_chained("channel:legacy") == journal.read_messages(
        "channel:legacy"
    )
    journal.append("other", "user", "outside dashboard", tab_id="not-indexed")
    assert journal.read_messages_chained("other")[0]["content"] == "outside dashboard"
    assert journal._tab_id_index["not-indexed"] == []


def test_delete_removes_actual_search_rows_for_aliases(journal_home):
    session_search.reset_for_tests()
    try:
        journal = ConversationLog(journal_home / "sessions")
        key = "dashboard:chat-search"
        journal.append(key, "user", "searchmarker")
        for alias in (key, key.replace(":", "_", 1)):
            assert session_search.index_session(alias, "Search", "searchmarker")
        assert len(session_search.search_sessions("searchmarker")) == 2
        assert journal.delete_session(key)
        assert session_search.search_sessions("searchmarker") == []
        assert journal.delete_session(key) is False
    finally:
        session_search.reset_for_tests()


def test_content_search_applies_persisted_and_live_restrictions(tmp_path):
    journal = ConversationLog(tmp_path)
    for key in ("persistent", "temporary", "incognito", "live"):
        journal.append(key, "user", "secretmarker")
    journal.update_metadata("temporary", {"memory_mode": "temporary"})
    journal.update_metadata("incognito", {"memory_mode": "incognito"})
    session_restrictions.mark_incognito("live")
    try:
        assert [row["key"] for row in journal.search_sessions("secretmarker")] == [
            "persistent"
        ]
    finally:
        session_restrictions.clear("live")
    assert {row["key"] for row in journal.search_sessions("secretmarker")} == {
        "persistent",
        "live",
    }


def test_source_window_skips_restricted_without_spending_five_session_budget(tmp_path):
    journal = ConversationLog(tmp_path)
    for number in range(7):
        key = f"dashboard:normal-{number}"
        journal.append(key, "user", f"public-{number}")
        os.utime(journal._path(key), (1000 + number, 1000 + number))
    for number in range(12):
        key = f"dashboard:private-{number}"
        journal.append(key, "user", f"private-{number}")
        journal.update_metadata(key, {"memory_mode": "temporary"})
        os.utime(journal._path(key), (2000 + number, 2000 + number))
    contents = {
        row["content"]
        for row in journal.recent_from_source("dashboard:", max_messages=50)
    }
    assert contents == {f"public-{number}" for number in range(2, 7)}
    excluded = {
        row["content"]
        for row in journal.recent_from_source(
            "dashboard:", exclude_key="dashboard:normal-6", max_messages=50
        )
    }
    assert excluded == {f"public-{number}" for number in range(1, 6)}


def test_source_window_uses_last_fifty_lines_and_scan_cap(tmp_path):
    journal = ConversationLog(tmp_path)
    journal.append("dashboard:tail", "user", "oldest")
    for number in range(55):
        journal.append("dashboard:tail", "assistant", str(number))
    recent = journal.recent_from_source("dashboard:", max_messages=100)
    assert len(recent) == 50 and recent[0]["content"] == "5"
    os.utime(journal._path("dashboard:tail"), (1000, 1000))
    for number in range(50):
        key = f"dashboard:private-{number}"
        journal.append(key, "user", "private")
        journal.update_metadata(key, {"memory_mode": "temporary"})
        os.utime(journal._path(key), (2000 + number, 2000 + number))
    assert journal.recent_from_source("dashboard:") == []


def test_summary_metadata_and_title_cache_keep_current_fallback_rules(tmp_path):
    journal = ConversationLog(tmp_path)
    path = journal._path("late")
    rows = [{"_type": "metadata", "agent": "operator", "created_at": "old"}]
    rows.extend({"role": "tool", "content": str(index)} for index in range(22))
    rows.append({"role": "user", "content": "late title"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert journal.list_sessions()[0]["title"] == "late"
    journal.read_messages("late")
    summary = journal.list_sessions()[0]
    assert summary["title"] == "late title"
    assert summary["agent"] == "operator" and summary["created"] == "old"
    assert summary["memory_mode"] == "persistent" and summary["messages"] >= 1
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(path.name)
    assert [row["key"] for row in journal.list_sessions()] == ["late"]


def test_retention_cleanup_is_rate_limited_and_preserves_fresh_files(
    tmp_path, monkeypatch
):
    directory = tmp_path / "archive"
    directory.mkdir()
    old, fresh = directory / "old.jsonl", directory / "fresh.jsonl"
    old.write_text("old")
    fresh.write_text("fresh")
    os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    monkeypatch.setattr(history, "_last_cleanup", time.time())
    assert history._cleanup_old_archives(base=tmp_path) == 0 and old.exists()
    monkeypatch.setattr(history, "_last_cleanup", 0.0)
    assert history._cleanup_old_archives(base=tmp_path) == 1
    assert not old.exists() and fresh.exists()


def test_journal_writes_keep_default_file_permissions(tmp_path):
    reference = tmp_path / "reference"
    reference.write_text("owned")
    permissions = stat.S_IMODE(reference.stat().st_mode)
    journal = ConversationLog(tmp_path)
    journal.append("owned", "user", "first")
    assert stat.S_IMODE(journal._path("owned").stat().st_mode) == permissions
    journal.update_metadata("owned", {"title": "Title"})
    assert stat.S_IMODE(journal._path("owned").stat().st_mode) == permissions
    journal.rewrite_session("owned", [])
    assert stat.S_IMODE(journal._path("owned").stat().st_mode) == permissions
    archive = next((tmp_path / "archive").glob("*.jsonl"))
    assert stat.S_IMODE(archive.stat().st_mode) == permissions
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "messages,count,sensitive",
    [
        (
            [{"role": "tool", "content": "read ~/.SSH/id_rsa", "tools": ["read"]}],
            1,
            True,
        ),
        ([{"role": "assistant", "content": "~/.aws/credentials"}], 0, False),
        (
            [{"role": "tool_result", "content": {"path": "~/.aws/credentials"}}],
            1,
            False,
        ),
        ([{"role": "assistant", "tools": [3, "read .npmrc"]}], 1, True),
        ([{"role": "tool_call", "content": "ls /tmp", "tools": "bad"}], 1, False),
    ],
)
def test_tool_activity_count_and_sensitive_patterns(messages, count, sensitive):
    assert history._count_tool_call_messages(messages) == count
    assert history._session_touched_sensitive(messages) is sensitive
