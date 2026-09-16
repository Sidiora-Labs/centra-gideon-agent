import json

import pytest

from gideon.cognition.history import ConversationLog
from gideon.engine import agent_metadata, session_organize, session_search
from gideon.engine.session_map import SessionMap, read_transcript, transcript_path
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.fixture
def catalog_home(tmp_path, monkeypatch):
    home = tmp_path / "catalog"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    session_search.reset_for_tests()
    yield home
    session_search.reset_for_tests()


def test_index_replacement_rolls_back_both_tables(catalog_home):
    assert session_search.index_session(
        "owned", "Original", "original apricot", mtime=7
    )
    connection = session_search._connect()
    connection.execute(
        "CREATE TRIGGER reject_owned_update BEFORE UPDATE ON indexed "
        "WHEN NEW.session_key = 'owned' BEGIN SELECT RAISE(ABORT, 'owned failure'); END"
    )
    assert not session_search.index_session(
        "owned", "New", "replacement kumquat", mtime=8
    )
    assert session_search.search_sessions("kumquat") == []
    assert [row["key"] for row in session_search.search_sessions("apricot")] == [
        "owned"
    ]
    row = connection.execute(
        "SELECT title, mtime FROM indexed WHERE session_key='owned'"
    ).fetchone()
    assert tuple(row) == ("Original", 7.0)
    connection.execute("DROP TRIGGER reject_owned_update")
    assert session_search.index_session("owned", "New", "replacement kumquat", mtime=8)
    assert session_search.search_sessions("apricot") == []
    assert session_search.stats()["sessions"] == 1


def test_orphan_sweep_includes_fts_only_rows(catalog_home):
    log = ConversationLog(base_dir=catalog_home / "transcripts")
    log.append("live", "user", "catalog nectarines")
    assert session_search.reindex_all(log) == 1
    connection = session_search._connect()
    connection.execute(
        "INSERT INTO sessions_fts(session_key, title, body) VALUES('orphan', 'gone', 'catalog nectarines')"
    )
    assert len(session_search.search_sessions("nectarines")) == 2
    assert session_search.purge_orphans(log) == 1
    assert [row["key"] for row in session_search.search_sessions("nectarines")] == [
        "live"
    ]


def test_corrupt_index_is_unavailable_and_can_be_rebuilt(catalog_home):
    session_search.db_path().write_bytes(b"not a sqlite database")
    assert session_search.stats() == {"available": False, "sessions": 0}
    assert session_search.search_sessions("anything") == []
    session_search.db_path().unlink()
    assert session_search.index_session("recovered", "Recovered", "fresh gooseberries")
    assert [row["key"] for row in session_search.search_sessions("gooseberries")] == [
        "recovered"
    ]


def test_search_connection_follows_the_active_home(catalog_home, tmp_path, monkeypatch):
    assert session_search.index_session("first", "First", "home strawberries")
    second = tmp_path / "second-home"
    monkeypatch.setenv("GIDEON_HOME", str(second))
    assert session_search.search_sessions("strawberries") == []
    assert session_search.index_session("second", "Second", "home blueberries")
    monkeypatch.setenv("GIDEON_HOME", str(catalog_home))
    assert session_search.search_sessions("blueberries") == []
    assert [row["key"] for row in session_search.search_sessions("strawberries")] == [
        "first"
    ]


def test_channel_link_collisions_and_identity_survive_reload(catalog_home):
    mapping = SessionMap()
    mapping.set("dashboard:one", "resume-one", provider="acp", cwd="/owned/workspace")
    mapping.set_channel_link("dashboard:one", "thread-old", "channel-one")
    mapping.set_channel_link("dashboard:two", "thread-old", "channel-two")
    assert mapping.get_session_for_thread("thread-old") == "dashboard:two"
    mapping.delete("dashboard:one")
    assert mapping.get_session_for_thread("thread-old") == "dashboard:two"
    mapping.set("dashboard:two", "resume-two", cwd="/owned/second")
    mapping.set_channel_link("dashboard:two", "thread-new", "channel-two")
    assert mapping.get_session_for_thread("thread-old") is None
    before = mapping._path.stat().st_mtime_ns
    mapping.set_channel_link("dashboard:two", "thread-new", "channel-two")
    assert mapping._path.stat().st_mtime_ns == before
    restored = SessionMap()
    assert restored.get("dashboard:dashboard_two") == "resume-two"
    assert restored.get_cwd("dashboard:two") == "/owned/second"
    assert restored.get_channel_link("dashboard:two") == ("thread-new", "channel-two")
    assert restored.prune() == 0


def test_map_load_filters_corrupt_entries_and_prunes_only_empty(catalog_home):
    mapping = SessionMap()
    mapping._path.write_text(
        json.dumps(
            {
                "sid-only": {"sid": "retained"},
                "thread-only": {"sid": "", "thread_ts": "10.2", "channel_id": "C"},
                "empty": {"sid": ""},
                "invalid": "no record",
                "missing-id-field": {"thread_ts": "10.3"},
            }
        )
    )
    restored = SessionMap()
    assert list(restored._data) == ["sid-only", "thread-only", "empty"]
    assert restored.prune() == 1
    assert restored.find_key_by_sid("retained") == "sid-only"
    assert restored.get_session_for_thread("10.2") == "thread-only"
    assert list(json.loads(mapping._path.read_text())) == ["sid-only", "thread-only"]


def test_transcript_parser_preserves_records_and_resolves_home_each_call(
    catalog_home, tmp_path, monkeypatch
):
    transcripts = catalog_home / "sessions"
    transcripts.mkdir()
    source = transcripts / "session-id.jsonl"
    source.write_text(
        '{"_type":"metadata","title":"Owned"}\ninvalid\n[]\n\n{"role":"user","content":"hello"}\n'
    )
    assert read_transcript(" session-id ") == [
        {"_type": "metadata", "title": "Owned"},
        {"role": "user", "content": "hello"},
    ]
    for invalid in ("", "..", "../session-id", "dir/session-id", "dir\\session-id"):
        assert transcript_path(invalid) is None
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "different"))
    assert transcript_path("session-id") is None


def test_metadata_notes_follow_home_and_preserve_sorted_snapshot(
    catalog_home, tmp_path, monkeypatch
):
    agent_metadata.save("zeta", "last\n")
    agent_metadata.save("alpha", "first\n")
    assert list(agent_metadata.load_all()) == ["alpha", "zeta"]
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "other-notes"))
    assert agent_metadata.load("alpha") == ""
    agent_metadata.save("alpha", "different")
    assert agent_metadata.delete("alpha") is True
    assert agent_metadata.delete("alpha") is False
    monkeypatch.setenv("GIDEON_HOME", str(catalog_home))
    assert agent_metadata.load("alpha") == "first\n"


@pytest.mark.asyncio
async def test_organization_accepts_through_real_session_and_history(catalog_home):
    log = ConversationLog(base_dir=catalog_home / "history")
    state = ConsoleState(sessions=None, start_time=0, conversation_log=log)
    state._folders = [{"id": "research", "name": "Research"}]
    state._tags = [{"id": "catalog", "name": "catalog", "color": "blue"}]
    session = _ChatSession("owned-chat", title="Research catalog")
    session.messages.append({"role": "user", "content": "Index the local catalog"})
    state._sessions[session.key] = session
    proposal = await session_organize.propose_for_session(
        state, session, allow_llm=False
    )
    assert proposal is not None
    assert (session.folder_id, session.tags) == ("", [])
    assert session_organize.apply_proposal(state, session, proposal) == {
        "folder_id": "research",
        "tags": ["catalog"],
    }
    metadata = log.get_metadata("dashboard:owned-chat")
    assert metadata["folder_id"] == "research"
    assert metadata["tags"] == ["catalog"]
    assert (
        await session_organize.propose_for_session(state, session, allow_llm=False)
        is None
    )


def test_real_decline_journal_and_closed_vocabulary_reply(catalog_home):
    session = _ChatSession("discussion", title="Quarterly work")
    folders = [{"id": "planning", "name": "Planning"}]
    tags = [
        {"id": "active", "name": "active", "status": True},
        {"id": "topic", "name": "topic"},
    ]
    proposal = session_organize.parse_llm_reply(
        "FOLDER: planning  TAGS: invented, active, topic, topic\nignored",
        session,
        folders,
        tags,
    )
    assert proposal is not None
    assert (proposal.folder_id, proposal.tag_names) == ("planning", ["topic"])
    session_organize.record_decline(proposal, now=123)
    assert session_organize.is_declined(proposal)
    document = json.loads(
        (catalog_home / "entity_settings" / "session_organize.json").read_text()
    )
    assert document["declined"][session_organize.dedup_key_for(proposal)] == 123.0
    other = session_organize.OrganizeProposal(session.key, folder_id="other")
    assert not session_organize.is_declined(other)
