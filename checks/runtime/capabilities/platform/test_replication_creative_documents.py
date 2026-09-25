import copy
import json

import pytest

from gideon.core.sqlite_compat import sqlite3
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform import replication_adapters
from gideon.workspace.capabilities.platform import replication_creative_documents as adapter


def _source(home):
    store = WorkStore(home)
    work = store.create({"request_id": "work-one", "title": "Canonical manuscript", "kind": "work",
        "prompt": "Write the ending.", "author_ref": None, "universe_ref": None, "active_draft_id": None})
    first = store.draft(work["id"], {"request_id": "draft-one", "revision": work["revision"],
        "text": "First immutable manuscript.\n", "note": "first"})
    second = store.draft(work["id"], {"request_id": "draft-two", "revision": first["work"]["revision"],
        "text": "Second immutable manuscript.\n", "note": "approved"})
    return store, second["work"], first["draft"], second["draft"]


def _entries(home):
    return [{"entry_id": entry_id, "rows": adapter.read_rows(home, entry_id)} for entry_id in adapter.ENTRIES]


def _replicate_current_work(source, target):
    rows = replication_adapters.read_rows(source, "creative.works")
    result = replication_adapters.apply_rows(target, "creative.works", rows, {}, conflicts.ConflictQueue(target),
        "2026-09-25T12:00:00+00:00")
    assert result.added == 1


def test_two_home_authored_text_and_complete_immutable_versions(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, first_draft, second_draft = _source(source)
    metadata = replication_adapters.read_rows(source, "creative.works")
    assert "First immutable manuscript" not in json.dumps(metadata)
    _replicate_current_work(source, target)

    entries = _entries(source)
    adapter.validate_entries(entries)
    version_result = adapter.apply_rows(target, adapter.VERSIONS_ENTRY, entries[0]["rows"], {},
        conflicts.ConflictQueue(target), "2026-09-25T12:01:00+00:00")
    draft_result = adapter.apply_rows(target, adapter.DRAFTS_ENTRY, entries[1]["rows"], {},
        conflicts.ConflictQueue(target), "2026-09-25T12:01:00+00:00")
    assert (version_result.added, draft_result.added) == (3, 2)

    target_store = WorkStore(target)
    assert target_store.revisions(work["id"]) == WorkStore(source).revisions(work["id"])
    assert target_store.read_draft(work["id"], first_draft["id"])["text"] == "First immutable manuscript.\n"
    assert target_store.read_draft(work["id"], second_draft["id"])["text"] == "Second immutable manuscript.\n"
    assert target_store.get(work["id"])["text"] == "Second immutable manuscript.\n"
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM work_requests").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM work_draft_requests").fetchone()[0] == 0

    omitted = adapter.apply_rows(target, adapter.DRAFTS_ENTRY, [], draft_result.new_ancestors,
        conflicts.ConflictQueue(target), "2026-09-25T12:02:00+00:00")
    assert (omitted.removed, len(adapter.read_rows(target, adapter.DRAFTS_ENTRY))) == (0, 2)


def test_complete_batch_hash_reference_and_immutable_collision_guards(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, _, _ = _source(source)
    entries = _entries(source)
    broken = copy.deepcopy(entries)
    broken[1]["rows"] = broken[1]["rows"][1:]
    with pytest.raises(ValueError, match="missing authored draft"):
        adapter.validate_entries(broken)
    broken = copy.deepcopy(entries)
    original = broken[1]["rows"][0]["data"]["text"]
    broken[1]["rows"][0]["data"]["text"] = ("X" if original[0] != "X" else "Y") + original[1:]
    with pytest.raises(ValueError, match="hash does not match"):
        adapter.validate_entries(broken)

    _replicate_current_work(source, target)
    for item in entries:
        adapter.apply_rows(target, item["entry_id"], item["rows"], {}, conflicts.ConflictQueue(target),
            "2026-09-25T12:03:00+00:00")
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        row = database.execute("SELECT record FROM work_revisions WHERE id=? AND revision=1", (work["id"],)).fetchone()
        changed = json.loads(row[0]); changed["title"] = "Divergent immutable title"
        database.execute("UPDATE work_revisions SET record=? WHERE id=? AND revision=1",
                         (json.dumps(changed, sort_keys=True), work["id"]))
    queue = conflicts.ConflictQueue(target)
    result = adapter.apply_rows(target, adapter.VERSIONS_ENTRY, entries[0]["rows"],
        {row["id"]: conflicts.row_sha(row) for row in entries[0]["rows"]}, queue,
        "2026-09-25T12:04:00+00:00")
    assert result.conflicts == 1 and result.updated == result.removed == 0
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW, entry_id=adapter.VERSIONS_ENTRY)
    assert len(pending) == 1 and pending[0].remote_row["data"]["record"]["title"] == "Canonical manuscript"
