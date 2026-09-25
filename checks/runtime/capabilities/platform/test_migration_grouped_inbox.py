import json

import pytest

from gideon.workspace.capabilities.platform import migration
from gideon.workspace.capabilities.platform.migration import MigrationError, commit, preview
from test_migration_grouped_song import mixed_archive, opened, reviewed


CAPTURE = "82828282-8282-4282-8282-828282828282"


def event_count(knowledge):
    return knowledge.db.execute(
        "SELECT count(*) FROM capability_knowledge_capture_events WHERE capture_id=?", (CAPTURE,)).fetchone()[0]


def test_canonical_inbox_song_groups_restart_and_replay_one_immutable_event(tmp_path):
    source, _ = mixed_archive(include_inbox=True)
    inspected = preview(source)
    assert [group["id"] for group in inspected["commit_groups"]] == ["canonical", "inbox", "song"]
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    receipt, created = commit(people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts)
    assert created is True and receipt["status"] == "complete" and receipt["domains"]["inbox"] == 1
    assert event_count(knowledge) == 1
    path = migration._grouped_root(repertoire) / f'{receipt["archive_digest"]}.json'
    before = path.read_bytes()
    knowledge.close()
    restarted = opened(home)
    try:
        replay, replay_created = commit(restarted[0], reviewed(source), *restarted[1:])
        assert replay_created is False and replay == receipt and path.read_bytes() == before
        assert event_count(restarted[1]) == 1
    finally:
        restarted[1].close()


def test_song_failure_retains_completed_inbox_and_retry_does_not_duplicate_event(tmp_path, monkeypatch):
    source, _ = mixed_archive(include_inbox=True)
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    original = migration._commit_song
    monkeypatch.setattr(migration, "_commit_song", lambda *args: (_ for _ in ()).throw(MigrationError("song paused", 503)))
    partial, created = commit(people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts)
    assert created is True and partial["status"] == "partial"
    assert [(group["id"], group["status"]) for group in partial["groups"]] == [
        ("canonical", "complete"), ("inbox", "complete"), ("song", "pending")]
    assert event_count(knowledge) == 1
    monkeypatch.setattr(migration, "_commit_song", original)
    complete, retry_created = commit(people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts)
    assert retry_created is True and complete["status"] == "complete" and event_count(knowledge) == 1
    knowledge.close()


def test_completed_inbox_drift_blocks_pending_song_without_deleting_event(tmp_path, monkeypatch):
    source, _ = mixed_archive(include_inbox=True)
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    original = migration._commit_song
    monkeypatch.setattr(migration, "_commit_song", lambda *args: (_ for _ in ()).throw(MigrationError("song paused", 503)))
    commit(people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts)
    knowledge.db.execute("UPDATE capability_knowledge_captures SET status='error',revision=2 WHERE id=?", (CAPTURE,))
    knowledge.db.commit()
    monkeypatch.setattr(migration, "_commit_song", original)
    with pytest.raises(MigrationError, match="inbox migration group has canonical identity drift"):
        commit(people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts)
    assert event_count(knowledge) == 1 and repertoire.list() == []
    journal = next(migration._grouped_root(repertoire).glob("*.json"))
    assert json.loads(journal.read_text())["groups"][1]["state"] == "complete"
    knowledge.close()
