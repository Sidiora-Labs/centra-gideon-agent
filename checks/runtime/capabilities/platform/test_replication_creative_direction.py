import copy
import hashlib
import json

import pytest

from gideon.core.sqlite_compat import sqlite3
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.store import CatalogError
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform import (
    replication_creative_direction as adapter,
)


def plan(extra=False):
    values = [
        {
            "id": "verify",
            "title": "Verify source",
            "operation": "source.verify",
            "depends_on": [],
        },
        {
            "id": "publish",
            "title": "Publish treatment",
            "operation": "treatment.snapshot",
            "depends_on": ["verify"],
        },
    ]
    if extra:
        values.append(
            {
                "id": "release",
                "title": "Verify release",
                "operation": "source.verify",
                "depends_on": ["publish"],
            }
        )
    return values


def prepare_work(home, suffix="one"):
    works = WorkStore(home)
    work = works.create(
        {
            "request_id": "work-" + suffix,
            "title": "Signal " + suffix,
            "kind": "work",
            "prompt": "",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    result = works.draft(
        work["id"],
        {
            "request_id": "draft-" + suffix,
            "revision": 1,
            "text": "The lighthouse answered " + suffix + ".",
            "note": "approved",
        },
    )
    return result["work"], result["draft"]


def create_project(home, suffix="one", complete=False):
    work, draft = prepare_work(home, suffix)
    store = DirectionStore(home)
    project = store.create(
        {
            "request_id": "direction-" + suffix,
            "name": "Night signal " + suffix,
            "treatment": "Cold blue light crosses a winter harbor.",
            "sources": [
                {"kind": "work", "id": work["id"], "revision": work["revision"]}
            ],
            "steps": plan(),
        }
    )
    if complete:
        project = store.control(project["id"], {"revision": 1, "action": "start"})
        project = store.advance(project["id"], {"revision": 2})
        project = store.advance(project["id"], {"revision": 3})
    return project, work, draft


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def by_name(values, name):
    return next(row for row in values if row["data"]["name"] == name)


def test_projection_preserves_current_pins_outputs_and_excludes_private_state(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    completed, work, draft = create_project(source, "complete", complete=True)
    draft_project, pending_work, pending_draft = create_project(source, "pending")
    projected = rows(source)
    assert len(projected) == 2
    adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": projected}])
    complete_row = by_name(projected, completed["name"])
    pending_row = by_name(projected, draft_project["name"])
    assert complete_row["id"] == completed["id"]
    assert complete_row["data"] == completed
    assert complete_row["data"]["revision"] == 4
    assert complete_row["data"]["status"] == "completed"
    assert [step["status"] for step in complete_row["data"]["steps"]] == [
        "done",
        "done",
    ]
    verify_result = complete_row["data"]["steps"][0]["result"]
    assert verify_result["verified_sources"] == [
        {"kind": "work", "id": work["id"], "revision": 2}
    ]
    assert verify_result["verified_at"].endswith("+00:00")
    output = complete_row["data"]["steps"][1]["result"]
    assert output["artifact_version"] == 1
    assert output["path"] == f'/api/artifacts/{output["artifact_id"]}?version=1'
    assert len(output["content_hash"]) == 64
    artifact = DirectionStore(source).artifacts.get(output["artifact_id"], version=1)
    assert artifact is not None
    assert artifact.readonly is True
    assert (
        hashlib.sha256(artifact.content.encode()).hexdigest() == output["content_hash"]
    )
    pin = complete_row["data"]["sources"][0]["chapters"][0]
    assert pin["work_id"] == work["id"]
    assert pin["work_revision"] == 2
    assert pin["draft_id"] == draft["id"]
    assert pin["artifact_id"] == draft["artifact_id"]
    assert pin["artifact_version"] == 1
    assert (
        pin["content_hash"]
        == hashlib.sha256(b"The lighthouse answered complete.").hexdigest()
    )
    assert (
        pending_row["data"]["sources"][0]["chapters"][0]["work_id"]
        == pending_work["id"]
    )
    assert (
        pending_row["data"]["sources"][0]["chapters"][0]["draft_id"]
        == pending_draft["id"]
    )
    wire = json.dumps(projected, sort_keys=True)
    assert "request_id" not in wire
    assert "credential" not in wire
    assert "trigger_id" not in wire
    assert "import_id" not in wire
    assert "The lighthouse answered complete." not in wire
    with sqlite3.connect(
        source / "capabilities/creative/direction.sqlite3"
    ) as database:
        assert (
            database.execute("SELECT count(*) FROM direction_requests").fetchone()[0]
            == 2
        )
    outcome = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        projected,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T08:00:00+00:00",
    )
    assert outcome.added == 2
    assert outcome.updated == 0
    assert outcome.removed == 0
    assert outcome.conflicts == 0
    assert len(outcome.new_ancestors) == 2
    assert DirectionStore(target).get(completed["id"]) == completed
    assert DirectionStore(target).get(draft_project["id"]) == draft_project
    with sqlite3.connect(
        target / "capabilities/creative/direction.sqlite3"
    ) as database:
        assert (
            database.execute("SELECT count(*) FROM direction_requests").fetchone()[0]
            == 0
        )
    assert WorkStore(target).list()["items"] == []
    assert (
        DirectionStore(target).artifacts.get(output["artifact_id"], version=1) is None
    )
    assert DirectionStore(target).artifacts.get(pin["artifact_id"], version=1) is None
    started = DirectionStore(target).control(
        draft_project["id"], {"revision": 1, "action": "start"}
    )
    assert started["status"] == "running"
    with pytest.raises(CatalogError) as unavailable:
        DirectionStore(target).advance(draft_project["id"], {"revision": 2})
    assert unavailable.value.status == 404
    unchanged = DirectionStore(target).get(draft_project["id"])
    assert unchanged["steps"][0]["status"] == "pending"
    assert WorkStore(target).list()["items"] == []


def test_two_home_conflict_selected_restore_and_tombstone_converge(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    project, _, _ = create_project(first, "shared")
    initial = rows(first)
    received = adapter.apply_rows(
        second,
        adapter.ENTRY_ID,
        initial,
        {},
        conflicts.ConflictQueue(second),
        "2026-09-25T09:00:00+00:00",
    )
    ancestor = received.new_ancestors
    assert ancestor == {project["id"]: conflicts.row_sha(initial[0])}
    local = DirectionStore(first).control(
        project["id"], {"revision": 1, "action": "start"}
    )
    remote = DirectionStore(second).replace_plan(
        project["id"], {"revision": 1, "steps": plan(extra=True)}
    )
    assert local["revision"] == 2 and local["status"] == "running"
    assert remote["revision"] == 2 and len(remote["steps"]) == 3
    queue = conflicts.ConflictQueue(first)
    conflicted = adapter.apply_rows(
        first,
        adapter.ENTRY_ID,
        rows(second),
        ancestor,
        queue,
        "2026-09-25T09:05:00+00:00",
    )
    assert conflicted.added == 0
    assert conflicted.updated == 0
    assert conflicted.removed == 0
    assert conflicted.conflicts == 1
    assert DirectionStore(first).get(project["id"]) == local
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1
    conflict = pending[0]
    assert conflict.entry_id == adapter.ENTRY_ID
    assert conflict.entity_id == project["id"]
    assert conflict.local_row["data"] == local
    assert conflict.remote_row["data"] == remote
    resolved = adapter.restore_fields(
        first, conflict.id, ["steps"], "2026-09-25T09:10:00+00:00"
    )
    assert resolved["resolved"] is True
    assert resolved["fields"] == ["steps"]
    merged = DirectionStore(first).get(project["id"])
    assert merged["status"] == "running"
    assert merged["steps"] == remote["steps"]
    assert merged["revision"] == 3
    assert merged["updated_at"] == "2026-09-25T09:10:00+00:00"
    assert queue.get(conflict.id).status == conflicts.STATUS_RESOLVED

    source, replica = tmp_path / "delete-source", tmp_path / "delete-replica"
    doomed, _, _ = create_project(source, "doomed")
    baseline = rows(source)
    copied = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        baseline,
        {},
        conflicts.ConflictQueue(replica),
        "2026-09-25T10:00:00+00:00",
    )
    assert DirectionStore(replica).get(doomed["id"]) == doomed
    adapter.write_row(source, adapter.ENTRY_ID, None, doomed["id"])
    assert rows(source) == []
    removed = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        [],
        copied.new_ancestors,
        conflicts.ConflictQueue(replica),
        "2026-09-25T10:05:00+00:00",
    )
    assert removed.added == 0
    assert removed.updated == 0
    assert removed.removed == 1
    assert removed.conflicts == 0
    assert rows(replica) == []
    with pytest.raises(CatalogError) as missing:
        DirectionStore(replica).get(doomed["id"])
    assert missing.value.status == 404


def test_two_home_fast_forward_and_replay_preserve_canonical_revision(tmp_path):
    source = tmp_path / "source"
    replica = tmp_path / "replica"
    project, _, _ = create_project(source, "fast-forward")
    initial_rows = rows(source)
    initial = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        initial_rows,
        {},
        conflicts.ConflictQueue(replica),
        "2026-09-25T10:10:00+00:00",
    )
    assert initial.added == 1
    assert initial.updated == 0
    assert initial.conflicts == 0
    assert initial.new_ancestors[project["id"]] == conflicts.row_sha(initial_rows[0])
    changed = DirectionStore(source).control(
        project["id"], {"revision": 1, "action": "start"}
    )
    remote_rows = rows(source)
    forwarded = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        remote_rows,
        initial.new_ancestors,
        conflicts.ConflictQueue(replica),
        "2026-09-25T10:15:00+00:00",
    )
    assert forwarded.added == 0
    assert forwarded.updated == 1
    assert forwarded.removed == 0
    assert forwarded.conflicts == 0
    assert DirectionStore(replica).get(project["id"]) == changed
    assert forwarded.new_ancestors[project["id"]] == conflicts.row_sha(remote_rows[0])
    replayed = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        remote_rows,
        forwarded.new_ancestors,
        conflicts.ConflictQueue(replica),
        "2026-09-25T10:20:00+00:00",
    )
    assert replayed.added == 0
    assert replayed.updated == 0
    assert replayed.removed == 0
    assert replayed.conflicts == 0
    assert replayed.new_ancestors == forwarded.new_ancestors


def test_complete_batch_preflight_rejects_invalid_pins_outputs_private_fields_and_duplicates(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    completed, _, _ = create_project(source, "validate", complete=True)
    canonical = rows(source)[0]

    invalid = []
    value = copy.deepcopy(canonical)
    value["data"]["sources"][0]["chapters"][0]["artifact_version"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["sources"][0]["chapters"][0]["content_hash"] = "bad"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["sources"][0]["chapters"][0]["chapter_id"] = "unexpected"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["sources"][0]["revision"] = True
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["sources"][0]["kind"] = "prompt"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["sources"] = []
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][0]["depends_on"] = ["later"]
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][0]["operation"] = "shell.execute"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][0]["result"]["verified_sources"] = []
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][0]["result"]["verified_at"] = "yesterday"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][1]["result"]["artifact_version"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][1]["result"]["content_hash"] = "0" * 63
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][1]["result"]["path"] = "/etc/passwd"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][1]["attempts"] = True
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["steps"][1]["status"] = "claimed"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["status"] = "executing"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["revision"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["updated_at"] = "2020-01-01T00:00:00+00:00"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["request_id"] = "private-ledger"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["sources"][0]["credential_ref"] = "secret"
    invalid.append(value)
    for row in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [row]}])
    assert rows(target) == []
    with pytest.raises(ValueError, match="complete entry coverage"):
        adapter.validate_entries([])
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([{"entry_id": "creative.other", "rows": [canonical]}])
    with pytest.raises(ValueError, match="unique"):
        adapter.validate_entries(
            [{"entry_id": adapter.ENTRY_ID, "rows": [canonical, canonical]}]
        )
    with pytest.raises(ValueError):
        adapter.apply_rows(
            target,
            adapter.ENTRY_ID,
            [invalid[0]],
            {},
            conflicts.ConflictQueue(target),
            "2026-09-25T11:00:00+00:00",
        )
    assert rows(target) == []
    assert DirectionStore(target).list() == []
    with sqlite3.connect(
        target / "capabilities/creative/direction.sqlite3"
    ) as database:
        assert (
            database.execute("SELECT count(*) FROM direction_requests").fetchone()[0]
            == 0
        )


def test_writer_uses_direction_authority_and_never_materializes_dependencies_or_ledgers(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    project, work, draft = create_project(source, "writer")
    row = rows(source)[0]
    adapter.write_row(target, adapter.ENTRY_ID, row, project["id"])
    reopened = DirectionStore(target)
    assert reopened.get(project["id"]) == project
    assert reopened.list() == [project]
    assert reopened.works.list()["items"] == []
    assert reopened.artifacts.get(draft["artifact_id"], version=1) is None
    with sqlite3.connect(
        target / "capabilities/creative/direction.sqlite3"
    ) as database:
        stored = json.loads(
            database.execute(
                "SELECT record FROM direction_projects WHERE id=?", (project["id"],)
            ).fetchone()[0]
        )
        assert stored == project
        assert (
            database.execute("SELECT count(*) FROM direction_requests").fetchone()[0]
            == 0
        )
    pin = stored["sources"][0]["chapters"][0]
    assert pin["work_id"] == work["id"]
    assert pin["draft_id"] == draft["id"]
    assert pin["artifact_id"] == draft["artifact_id"]
    assert pin["artifact_version"] == 1
    with pytest.raises(CatalogError) as unavailable:
        reopened.pin({"kind": "work", "id": work["id"], "revision": work["revision"]})
    assert unavailable.value.status == 404
    assert reopened.get(project["id"]) == project
    assert reopened.works.list()["items"] == []
    adapter.write_row(target, adapter.ENTRY_ID, None, project["id"])
    assert reopened.list() == []
    with sqlite3.connect(
        target / "capabilities/creative/direction.sqlite3"
    ) as database:
        assert (
            database.execute("SELECT count(*) FROM direction_requests").fetchone()[0]
            == 0
        )
