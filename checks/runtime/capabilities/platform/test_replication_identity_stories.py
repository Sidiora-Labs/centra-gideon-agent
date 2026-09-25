import copy
import sqlite3

import pytest

from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.identity.store import StoryStore
from gideon.workspace.capabilities.platform import (
    replication_identity_stories as adapter,
)

NOW = "2026-09-25T12:00:00+00:00"


def store(home):
    return StoryStore(home / "capabilities/identity/stories.sqlite3")


def create_chain(home, suffix="one"):
    stories = store(home)
    root = stories.create(
        prompt="Where did this begin?",
        theme="Origins " + suffix,
        text="I learned to navigate by watching the harbor lights.",
        request_id="root-" + suffix,
    )
    child = stories.create(
        prompt="What followed?",
        theme="Growth " + suffix,
        text="The first long crossing made the lesson concrete.",
        parent_id=root["id"],
        request_id="child-" + suffix,
    )
    return stories, root, child


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def row_for(home, identity):
    return next(row for row in rows(home) if row["id"] == identity)


def edit(story, **changes):
    values = {key: story[key] for key in ("prompt", "theme", "text", "parent_id")}
    values.update(changes)
    return {"expected_revision": story["revision"], **values}


def test_two_home_projection_preserves_current_revision_and_parent_lineage_only(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store, root, child = create_chain(source)
    root = source_store.update(
        root["id"], **edit(root, text="The harbor lights became a lasting rule.")
    )

    projected = rows(source)
    assert [row["id"] for row in projected] == [root["id"], child["id"]]
    assert row_for(source, root["id"])["data"] == root
    assert row_for(source, child["id"])["data"]["parent_id"] == root["id"]
    assert row_for(source, root["id"])["data"]["revision"] == 2

    result = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        projected,
        {},
        conflicts.ConflictQueue(target),
        NOW,
    )
    assert (result.added, result.updated, result.removed, result.conflicts) == (
        2,
        0,
        0,
        0,
    )
    assert rows(target) == projected
    assert store(target).chain(child["id"]) == [root, child]

    source_path = source / "capabilities/identity/stories.sqlite3"
    target_path = target / "capabilities/identity/stories.sqlite3"
    with sqlite3.connect(source_path) as database:
        assert database.execute("SELECT count(*) FROM revisions").fetchone()[0] == 3
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 2
    with sqlite3.connect(target_path) as database:
        assert database.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_fast_forward_exact_replay_conflict_restore_and_restart(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store, root, child = create_chain(source, "shared")
    baseline = rows(source)
    initial = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        baseline,
        {},
        conflicts.ConflictQueue(target),
        NOW,
    )
    assert initial.new_ancestors == {
        row["id"]: conflicts.row_sha(row) for row in baseline
    }

    changed = source_store.update(
        child["id"], **edit(child, text="Source remembers the northern crossing.")
    )
    forwarded_rows = rows(source)
    forwarded = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        forwarded_rows,
        initial.new_ancestors,
        conflicts.ConflictQueue(target),
        "2026-09-25T12:05:00+00:00",
    )
    assert (forwarded.added, forwarded.updated, forwarded.conflicts) == (0, 1, 0)
    assert store(target).get(child["id"]) == changed
    replayed = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        forwarded_rows,
        forwarded.new_ancestors,
        conflicts.ConflictQueue(target),
        "2026-09-25T12:10:00+00:00",
    )
    assert (replayed.added, replayed.updated, replayed.removed, replayed.conflicts) == (
        0,
        0,
        0,
        0,
    )
    assert replayed.new_ancestors == forwarded.new_ancestors

    local_store = StoryStore(target / "capabilities/identity/stories.sqlite3")
    local = local_store.update(
        changed["id"], **edit(changed, theme="Local interpretation")
    )
    remote = source_store.update(
        changed["id"], **edit(changed, text="Source preserves exact weather and route.")
    )
    queue = conflicts.ConflictQueue(target)
    held = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        rows(source),
        forwarded.new_ancestors,
        queue,
        "2026-09-25T12:15:00+00:00",
    )
    assert (held.added, held.updated, held.removed, held.conflicts) == (0, 0, 0, 1)
    assert store(target).get(child["id"]) == local
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1
    assert pending[0].local_row["data"] == local
    assert pending[0].remote_row["data"] == remote

    resolved = adapter.restore_fields(
        target,
        pending[0].id,
        ["text"],
        "2026-09-25T12:20:00+00:00",
    )
    merged = StoryStore(target / "capabilities/identity/stories.sqlite3").get(
        child["id"]
    )
    assert resolved["fields"] == ["text"]
    assert merged["theme"] == "Local interpretation"
    assert merged["text"] == remote["text"]
    assert merged["parent_id"] == root["id"]
    assert merged["revision"] == local["revision"] + 1
    assert merged["updated_at"] == "2026-09-25T12:20:00+00:00"
    assert (
        conflicts.ConflictQueue(target).get(pending[0].id).status
        == conflicts.STATUS_RESOLVED
    )


def test_tombstone_deletes_children_before_parents_and_survives_restart(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store, root, child = create_chain(source, "delete")
    baseline = rows(source)
    copied = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        baseline,
        {},
        conflicts.ConflictQueue(target),
        NOW,
    )
    source_store.delete(child["id"], expected_revision=child["revision"])
    source_store.delete(root["id"], expected_revision=root["revision"])
    assert rows(source) == []

    removed = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        [],
        copied.new_ancestors,
        conflicts.ConflictQueue(target),
        "2026-09-25T13:00:00+00:00",
    )
    assert (removed.added, removed.updated, removed.removed, removed.conflicts) == (
        0,
        0,
        2,
        0,
    )
    assert removed.new_ancestors == {}
    assert StoryStore(target / "capabilities/identity/stories.sqlite3").list() == []
    with sqlite3.connect(target / "capabilities/identity/stories.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM stories").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_complete_batch_validation_rejects_orphans_cycles_private_fields_and_bad_lineage(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    _, root, child = create_chain(source, "validation")
    canonical = rows(source)
    root_row = copy.deepcopy(next(row for row in canonical if row["id"] == root["id"]))
    child_row = copy.deepcopy(
        next(row for row in canonical if row["id"] == child["id"])
    )

    invalid_sets = []
    invalid_sets.append([child_row])
    cyclic_root, cyclic_child = copy.deepcopy(root_row), copy.deepcopy(child_row)
    cyclic_root["data"]["parent_id"] = child["id"]
    cyclic_child["data"]["parent_id"] = root["id"]
    invalid_sets.append([cyclic_root, cyclic_child])
    private = copy.deepcopy(root_row)
    private["data"]["credential_ref"] = "HOST_SECRET"
    invalid_sets.append([private])
    authority = copy.deepcopy(root_row)
    authority["data"]["request_id"] = "execute-story"
    invalid_sets.append([authority])
    revision = copy.deepcopy(root_row)
    revision["data"]["revision"] = True
    invalid_sets.append([revision])
    timestamp = copy.deepcopy(root_row)
    timestamp["data"]["updated_at"] = "yesterday"
    invalid_sets.append([timestamp])
    identity = copy.deepcopy(root_row)
    identity["data"]["id"] = "f" * 32
    invalid_sets.append([identity])
    duplicate = [root_row, copy.deepcopy(root_row)]
    invalid_sets.append(duplicate)

    for invalid in invalid_sets:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": invalid}])
        with pytest.raises(ValueError):
            adapter.apply_rows(
                target,
                adapter.ENTRY_ID,
                invalid,
                {},
                conflicts.ConflictQueue(target),
                NOW,
            )
        assert rows(target) == []
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([])
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([{"entry_id": "identity.other", "rows": canonical}])


def test_referential_failure_is_preflighted_before_conflict_or_mutation(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_store, root, child = create_chain(source, "held-child")
    baseline = rows(source)
    copied = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        baseline,
        {},
        conflicts.ConflictQueue(target),
        NOW,
    )
    target_store = store(target)
    local_child = target_store.update(
        child["id"], **edit(child, text="Local child changed.")
    )
    source_store.delete(child["id"], expected_revision=child["revision"])
    source_store.delete(root["id"], expected_revision=root["revision"])
    queue = conflicts.ConflictQueue(target)

    with pytest.raises(ValueError, match="parent is missing"):
        adapter.apply_rows(
            target,
            adapter.ENTRY_ID,
            [],
            copied.new_ancestors,
            queue,
            "2026-09-25T13:10:00+00:00",
        )
    assert target_store.get(root["id"]) == root
    assert target_store.get(child["id"]) == local_child
    assert queue.items(status=conflicts.STATUS_NEEDS_REVIEW) == []


def test_writer_refuses_parent_first_violation_and_never_materializes_history_or_requests(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    _, root, child = create_chain(source, "writer")
    root_row, child_row = row_for(source, root["id"]), row_for(source, child["id"])
    with pytest.raises(ValueError, match="parent is missing"):
        adapter.write_row(target, adapter.ENTRY_ID, child_row, child["id"])
    adapter.write_row(target, adapter.ENTRY_ID, root_row, root["id"])
    adapter.write_row(target, adapter.ENTRY_ID, child_row, child["id"])
    with pytest.raises(ValueError, match="children"):
        adapter.write_row(target, adapter.ENTRY_ID, None, root["id"])
    adapter.write_row(target, adapter.ENTRY_ID, None, child["id"])
    adapter.write_row(target, adapter.ENTRY_ID, None, root["id"])
    with sqlite3.connect(target / "capabilities/identity/stories.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM stories").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
