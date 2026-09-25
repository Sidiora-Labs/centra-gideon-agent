import copy
import json

import pytest

from gideon.core.sqlite_compat import sqlite3
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform import (
    replication_adapters,
    replication_creative_documents,
)


def _source(home):
    store = WorkStore(home)
    work = store.create(
        {
            "request_id": "work-one",
            "title": "Canonical manuscript",
            "kind": "work",
            "prompt": "Write the ending.",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    first = store.draft(
        work["id"],
        {
            "request_id": "draft-one",
            "revision": work["revision"],
            "text": "First immutable manuscript.\n",
            "note": "first",
        },
    )
    second = store.draft(
        work["id"],
        {
            "request_id": "draft-two",
            "revision": first["work"]["revision"],
            "text": "Second immutable manuscript.\n",
            "note": "approved",
        },
    )
    return store, second["work"], first["draft"], second["draft"]


def _entries(home):
    return [
        {
            "entry_id": entry_id,
            "rows": replication_creative_documents.read_rows(home, entry_id),
        }
        for entry_id in replication_creative_documents.ENTRIES
    ]


def _replicate_current_work(source, target):
    rows = replication_adapters.read_rows(source, "creative.works")
    result = replication_adapters.apply_rows(
        target,
        "creative.works",
        rows,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T12:00:00+00:00",
    )
    assert result.added == 1


def test_two_home_authored_text_and_complete_immutable_versions(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, first_draft, second_draft = _source(source)
    metadata = replication_adapters.read_rows(source, "creative.works")
    assert "First immutable manuscript" not in json.dumps(metadata)
    _replicate_current_work(source, target)

    entries = _entries(source)
    replication_creative_documents.validate_entries(entries)
    version_result = replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.VERSIONS_ENTRY,
        entries[0]["rows"],
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T12:01:00+00:00",
    )
    draft_result = replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.DRAFTS_ENTRY,
        entries[1]["rows"],
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T12:01:00+00:00",
    )
    assert (version_result.added, draft_result.added) == (3, 2)

    target_store = WorkStore(target)
    assert target_store.revisions(work["id"]) == WorkStore(source).revisions(work["id"])
    assert (
        target_store.read_draft(work["id"], first_draft["id"])["text"]
        == "First immutable manuscript.\n"
    )
    assert (
        target_store.read_draft(work["id"], second_draft["id"])["text"]
        == "Second immutable manuscript.\n"
    )
    assert target_store.get(work["id"])["text"] == "Second immutable manuscript.\n"
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM work_requests").fetchone()[0] == 0
        assert (
            database.execute("SELECT count(*) FROM work_draft_requests").fetchone()[0]
            == 0
        )

    omitted = replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.DRAFTS_ENTRY,
        [],
        draft_result.new_ancestors,
        conflicts.ConflictQueue(target),
        "2026-09-25T12:02:00+00:00",
    )
    assert (
        omitted.removed,
        len(
            replication_creative_documents.read_rows(
                target, replication_creative_documents.DRAFTS_ENTRY
            )
        ),
    ) == (
        0,
        2,
    )


def test_complete_batch_hash_reference_and_immutable_collision_guards(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, _, _ = _source(source)
    entries = _entries(source)
    broken = copy.deepcopy(entries)
    broken[1]["rows"] = broken[1]["rows"][1:]
    with pytest.raises(ValueError, match="missing authored draft"):
        replication_creative_documents.validate_entries(broken)
    broken = copy.deepcopy(entries)
    original = broken[1]["rows"][0]["data"]["text"]
    broken[1]["rows"][0]["data"]["text"] = (
        "X" if original[0] != "X" else "Y"
    ) + original[1:]
    with pytest.raises(ValueError, match="hash does not match"):
        replication_creative_documents.validate_entries(broken)

    _replicate_current_work(source, target)
    for item in entries:
        replication_creative_documents.apply_rows(
            target,
            item["entry_id"],
            item["rows"],
            {},
            conflicts.ConflictQueue(target),
            "2026-09-25T12:03:00+00:00",
        )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        row = database.execute(
            "SELECT record FROM work_revisions WHERE id=? AND revision=1", (work["id"],)
        ).fetchone()
        changed = json.loads(row[0])
        changed["title"] = "Divergent immutable title"
        database.execute(
            "UPDATE work_revisions SET record=? WHERE id=? AND revision=1",
            (json.dumps(changed, sort_keys=True), work["id"]),
        )
    queue = conflicts.ConflictQueue(target)
    result = replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.VERSIONS_ENTRY,
        entries[0]["rows"],
        {row["id"]: conflicts.row_sha(row) for row in entries[0]["rows"]},
        queue,
        "2026-09-25T12:04:00+00:00",
    )
    assert result.conflicts == 1 and result.updated == result.removed == 0
    pending = queue.items(
        status=conflicts.STATUS_NEEDS_REVIEW,
        entry_id=replication_creative_documents.VERSIONS_ENTRY,
    )
    assert (
        len(pending) == 1
        and pending[0].remote_row["data"]["record"]["title"] == "Canonical manuscript"
    )


def test_owner_revision_import_rejects_noncanonical_current_and_reference_state(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, _, _ = _source(source)
    _replicate_current_work(source, target)
    rows = replication_creative_documents.read_rows(
        source, replication_creative_documents.VERSIONS_ENTRY
    )
    store = WorkStore(target)

    newer = copy.deepcopy(rows[-1]["data"]["record"])
    newer["revision"] = work["revision"] + 1
    with pytest.raises(ValueError, match="exceeds the canonical current work"):
        store.import_revision(newer)

    naive = copy.deepcopy(rows[0]["data"]["record"])
    naive["created_at"] = "2026-09-25T12:00:00"
    with pytest.raises(ValueError, match="timestamp"):
        store.import_revision(naive)

    reversed_time = copy.deepcopy(rows[0]["data"]["record"])
    reversed_time["created_at"] = "2026-09-25T13:00:00+00:00"
    reversed_time["updated_at"] = "2026-09-25T12:00:00+00:00"
    with pytest.raises(ValueError, match="predates"):
        store.import_revision(reversed_time)

    missing_pin = copy.deepcopy(rows[0]["data"]["record"])
    missing_pin["author_ref"] = {"id": "missing-author", "revision": 1}
    with pytest.raises(ValueError, match="Pinned context revision not found"):
        store.import_revision(missing_pin)

    mismatched = copy.deepcopy(rows[-1]["data"]["record"])
    mismatched["title"] = "Different current revision"
    with pytest.raises(ValueError, match="Current work does not match"):
        store.import_revision(mismatched)

    valid = rows[0]["data"]["record"]
    assert store.import_revision(valid) == valid
    assert store.import_revision(valid) == valid
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute(
            "SELECT count(*) FROM work_revisions WHERE id=? AND revision=1",
            (work["id"],),
        ).fetchone() == (1,)


def test_complete_coverage_rejects_order_gaps_duplicates_and_private_fields(tmp_path):
    source = tmp_path / "source"
    _source(source)
    entries = _entries(source)

    with pytest.raises(ValueError, match="complete ordered coverage"):
        replication_creative_documents.validate_entries(list(reversed(entries)))
    with pytest.raises(ValueError, match="complete ordered coverage"):
        replication_creative_documents.validate_entries(entries[:1])

    extra_entry_field = copy.deepcopy(entries)
    extra_entry_field[0]["private"] = "request-receipt"
    with pytest.raises(ValueError, match="complete ordered coverage"):
        replication_creative_documents.validate_entries(extra_entry_field)

    duplicate_version = copy.deepcopy(entries)
    duplicate_version[0]["rows"].append(copy.deepcopy(duplicate_version[0]["rows"][0]))
    with pytest.raises(ValueError, match="Duplicate creative document row"):
        replication_creative_documents.validate_entries(duplicate_version)

    discontinuous = copy.deepcopy(entries)
    discontinuous[0]["rows"] = [
        discontinuous[0]["rows"][0],
        discontinuous[0]["rows"][2],
    ]
    with pytest.raises(ValueError, match="complete and contiguous"):
        replication_creative_documents.validate_entries(discontinuous)

    private_version = copy.deepcopy(entries)
    private_version[0]["rows"][0]["data"]["request_id"] = "private"
    with pytest.raises(ValueError, match="Invalid work version data"):
        replication_creative_documents.validate_entries(private_version)

    private_draft = copy.deepcopy(entries)
    private_draft[1]["rows"][0]["data"]["provider_job"] = {"id": "job"}
    with pytest.raises(ValueError, match="Invalid authored draft data"):
        replication_creative_documents.validate_entries(private_draft)

    orphan_draft = copy.deepcopy(entries)
    for row in orphan_draft[1]["rows"]:
        row["data"]["work_id"] = "other-work"
    with pytest.raises(ValueError, match="missing authored draft"):
        replication_creative_documents.validate_entries(orphan_draft)


def test_version_dependency_preflight_is_atomic_before_any_history_write(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, _, _ = _source(source)
    _replicate_current_work(source, target)
    rows = copy.deepcopy(
        replication_creative_documents.read_rows(
            source, replication_creative_documents.VERSIONS_ENTRY
        )
    )
    last = rows[-1]["data"]
    last["record"]["universe_ref"] = {"id": "absent-universe", "revision": 7}
    last["record_sha256"] = replication_creative_documents._sha(last["record"])
    rows[-1]["id"] = replication_creative_documents._version_id(
        last["work_id"], last["revision"]
    )

    with pytest.raises(ValueError, match="pinned context is unavailable"):
        replication_creative_documents.apply_rows(
            target,
            replication_creative_documents.VERSIONS_ENTRY,
            rows,
            {},
            conflicts.ConflictQueue(target),
            "2026-09-25T13:00:00+00:00",
        )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute(
            "SELECT count(*) FROM work_revisions WHERE id=?", (work["id"],)
        ).fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM work_drafts WHERE work_id=?", (work["id"],)
        ).fetchone() == (0,)


def test_owner_draft_import_is_idempotent_and_refuses_content_or_provenance_drift(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, _, _ = _source(source)
    _replicate_current_work(source, target)
    row = replication_creative_documents.read_rows(
        source, replication_creative_documents.DRAFTS_ENTRY
    )[0]
    data = row["data"]
    store = WorkStore(target)

    assert (
        store.import_draft(
            data["work_id"],
            data["draft"],
            data["text"],
            data["artifact_description"],
            data["content_sha256"],
        )
        == data["draft"]
    )
    assert (
        store.import_draft(
            data["work_id"],
            data["draft"],
            data["text"],
            data["artifact_description"],
            data["content_sha256"],
        )
        == data["draft"]
    )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute(
            "SELECT count(*) FROM work_drafts WHERE id=?", (row["id"],)
        ).fetchone() == (1,)
    assert store.read_draft(work["id"], row["id"])["text"] == data["text"]

    altered = ("X" if data["text"][0] != "X" else "Y") + data["text"][1:]
    with pytest.raises(ValueError, match="hash does not match"):
        store.import_draft(
            data["work_id"],
            data["draft"],
            altered,
            data["artifact_description"],
            data["content_sha256"],
        )
    with pytest.raises(ValueError, match="provenance"):
        store.import_draft(
            data["work_id"],
            data["draft"],
            data["text"],
            "not-a-digest",
            data["content_sha256"],
        )
    bad_metadata = copy.deepcopy(data["draft"])
    bad_metadata["artifact_version"] = 2
    with pytest.raises(ValueError, match="artifact reference"):
        store.import_draft(
            data["work_id"],
            bad_metadata,
            data["text"],
            data["artifact_description"],
            data["content_sha256"],
        )
    wrong_size = copy.deepcopy(data["draft"])
    wrong_size["characters"] += 1
    with pytest.raises(ValueError, match="draft content"):
        store.import_draft(
            data["work_id"],
            wrong_size,
            data["text"],
            data["artifact_description"],
            data["content_sha256"],
        )


def test_draft_preflight_refuses_artifact_collision_without_database_mutation(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, _, _ = _source(source)
    _replicate_current_work(source, target)
    rows = replication_creative_documents.read_rows(
        source, replication_creative_documents.DRAFTS_ENTRY
    )
    first = rows[0]["data"]
    target_store = WorkStore(target)
    target_store.artifacts.create(
        name="Collision",
        slug=first["draft"]["artifact_id"],
        kind="markdown",
        content="Locally different content",
        description=first["artifact_description"],
        readonly=True,
    )

    with pytest.raises(ValueError, match="artifact conflicts"):
        replication_creative_documents.apply_rows(
            target,
            replication_creative_documents.DRAFTS_ENTRY,
            rows,
            {},
            conflicts.ConflictQueue(target),
            "2026-09-25T13:01:00+00:00",
        )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute(
            "SELECT count(*) FROM work_drafts WHERE work_id=?", (work["id"],)
        ).fetchone() == (0,)
    artifact = target_store.artifacts.get(first["draft"]["artifact_id"], version=1)
    assert artifact is not None and artifact.content == "Locally different content"


def test_draft_divergence_queues_conflict_and_missing_artifact_fails_export(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _, work, first_draft, _ = _source(source)
    _replicate_current_work(source, target)
    versions = replication_creative_documents.read_rows(
        source, replication_creative_documents.VERSIONS_ENTRY
    )
    drafts = replication_creative_documents.read_rows(
        source, replication_creative_documents.DRAFTS_ENTRY
    )
    replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.VERSIONS_ENTRY,
        versions,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T13:02:00+00:00",
    )
    baseline = replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.DRAFTS_ENTRY,
        drafts,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T13:02:00+00:00",
    )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        stored = database.execute(
            "SELECT record FROM work_drafts WHERE id=?", (first_draft["id"],)
        ).fetchone()
        changed = json.loads(stored[0])
        changed["note"] = "local immutable divergence"
        database.execute(
            "UPDATE work_drafts SET record=? WHERE id=?",
            (json.dumps(changed, sort_keys=True), first_draft["id"]),
        )
    queue = conflicts.ConflictQueue(target)
    held = replication_creative_documents.apply_rows(
        target,
        replication_creative_documents.DRAFTS_ENTRY,
        drafts,
        baseline.new_ancestors,
        queue,
        "2026-09-25T13:03:00+00:00",
    )
    assert (held.added, held.updated, held.removed, held.conflicts) == (0, 0, 0, 1)
    pending = queue.items(
        status=conflicts.STATUS_NEEDS_REVIEW,
        entry_id=replication_creative_documents.DRAFTS_ENTRY,
    )
    assert (
        len(pending) == 1
        and pending[0].local_row["data"]["draft"]["note"]
        == "local immutable divergence"
    )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert (
            json.loads(
                database.execute(
                    "SELECT record FROM work_drafts WHERE id=?", (first_draft["id"],)
                ).fetchone()[0]
            )["note"]
            == "local immutable divergence"
        )

    artifact = WorkStore(source).artifacts.get(first_draft["artifact_id"], version=1)
    assert artifact is not None
    version_path = (
        source / "artifacts" / first_draft["artifact_id"] / "versions" / "v1.html"
    )
    version_path.unlink()
    with pytest.raises(ValueError, match="artifact is unavailable"):
        replication_creative_documents.read_rows(
            source, replication_creative_documents.DRAFTS_ENTRY
        )
