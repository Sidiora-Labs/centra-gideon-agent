import copy
import sqlite3

import pytest

from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.experience.store import ExperienceStore
from gideon.workspace.capabilities.platform import replication_experience_stories as adapter


NOW = "2026-09-25T16:00:00+00:00"


def graph(title="The divided road"):
    return {
        "title": title,
        "start_node": "crossroads",
        "nodes": [
            {"id": "crossroads", "text": "Choose a road.", "kind": "scene", "choices": [
                {"id": "east", "label": "Walk east", "target": "orchard"},
                {"id": "west", "label": "Walk west", "target": "lake"},
            ]},
            {"id": "orchard", "text": "You reach the orchard.", "kind": "ending", "choices": []},
            {"id": "lake", "text": "You reach the lake.", "kind": "ending", "choices": []},
        ],
    }


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def test_two_real_homes_copy_only_current_authored_graphs(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store = ExperienceStore(source)
    story = source_store.save(graph())
    source_store.start({"story_id": story["id"], "story_revision": story["revision"], "request_id": "play-one"})

    projected = rows(source)
    adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": projected}])
    result = adapter.apply_rows(target, adapter.ENTRY_ID, projected, {}, conflicts.ConflictQueue(target), NOW)

    assert (result.added, result.updated, result.removed, result.conflicts) == (1, 0, 0, 0)
    assert ExperienceStore(target).story(story["id"]) == story
    with sqlite3.connect(target / "capabilities/experience.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM stories").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM versions").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_fast_forward_conflict_restore_and_exact_graph_validation(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store = ExperienceStore(source)
    original = source_store.save(graph())
    baseline = rows(source)
    copied = adapter.apply_rows(target, adapter.ENTRY_ID, baseline, {}, conflicts.ConflictQueue(target), NOW)

    remote = source_store.save({**graph("Remote title"), "revision": original["revision"]}, original["id"])
    forwarded = adapter.apply_rows(
        target, adapter.ENTRY_ID, rows(source), copied.new_ancestors,
        conflicts.ConflictQueue(target), "2026-09-25T16:05:00+00:00",
    )
    assert (forwarded.added, forwarded.updated, forwarded.conflicts) == (0, 1, 0)
    assert ExperienceStore(target).story(original["id"]) == remote

    local_store = ExperienceStore(target)
    local = local_store.save({**graph("Local title"), "revision": remote["revision"]}, original["id"])
    changed = copy.deepcopy(graph("Remote title"))
    changed["nodes"][1]["text"] = "The source orchard is in bloom."
    remote = source_store.save({**changed, "revision": remote["revision"]}, original["id"])
    queue = conflicts.ConflictQueue(target)
    held = adapter.apply_rows(
        target, adapter.ENTRY_ID, rows(source), forwarded.new_ancestors, queue,
        "2026-09-25T16:10:00+00:00",
    )
    assert (held.added, held.updated, held.removed, held.conflicts) == (0, 0, 0, 1)
    assert ExperienceStore(target).story(original["id"]) == local
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1

    resolved = adapter.restore_fields(
        target, pending[0].id, ["nodes"], "2026-09-25T16:15:00+00:00",
    )
    merged = ExperienceStore(target).story(original["id"])
    assert resolved["fields"] == ["nodes"]
    assert merged["title"] == "Local title"
    assert merged["nodes"] == remote["nodes"]
    assert merged["transitions"] == remote["transitions"]
    assert merged["revision"] == local["revision"] + 1
    assert conflicts.ConflictQueue(target).get(pending[0].id).status == conflicts.STATUS_RESOLVED


def test_tombstone_preserves_local_session_and_its_pinned_story_revision(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store = ExperienceStore(source)
    story = source_store.save(graph())
    baseline = rows(source)
    copied = adapter.apply_rows(target, adapter.ENTRY_ID, baseline, {}, conflicts.ConflictQueue(target), NOW)
    target_store = ExperienceStore(target)
    session = target_store.start({
        "story_id": story["id"], "story_revision": story["revision"], "request_id": "local-play",
    })["session"]

    source_store.delete(story["id"], story["revision"])
    removed = adapter.apply_rows(
        target, adapter.ENTRY_ID, [], copied.new_ancestors, conflicts.ConflictQueue(target),
        "2026-09-25T16:20:00+00:00",
    )
    assert (removed.added, removed.updated, removed.removed, removed.conflicts) == (0, 0, 1, 0)
    assert rows(target) == []
    assert target_store.session(session["id"])["story"] == story
    with sqlite3.connect(target / "capabilities/experience.sqlite3") as database:
        assert database.execute("SELECT deleted FROM stories WHERE id=?", (story["id"],)).fetchone() == (1,)
        assert database.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 1


def test_rejects_dangling_graphs_private_fields_and_identity_mismatch_before_write(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    story = ExperienceStore(source).save(graph())
    canonical = rows(source)[0]
    invalid = []
    dangling = copy.deepcopy(canonical)
    dangling["data"]["nodes"][0]["choices"][0]["target"] = "missing"
    invalid.append(dangling)
    private = copy.deepcopy(canonical)
    private["data"]["session_id"] = "runtime-authority"
    invalid.append(private)
    mismatch = copy.deepcopy(canonical)
    mismatch["data"]["id"] = "another-story"
    invalid.append(mismatch)
    noncanonical = copy.deepcopy(canonical)
    noncanonical["data"]["transitions"] = []
    invalid.append(noncanonical)

    for row in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [row]}])
        with pytest.raises(ValueError):
            adapter.apply_rows(target, adapter.ENTRY_ID, [row], {}, conflicts.ConflictQueue(target), NOW)
        assert rows(target) == []
    duplicate = [canonical, copy.deepcopy(canonical)]
    with pytest.raises(ValueError, match="Duplicate"):
        adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": duplicate}])
    assert story["id"] == canonical["id"]
