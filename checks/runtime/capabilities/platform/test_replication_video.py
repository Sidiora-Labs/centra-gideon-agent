import copy
import io
import json
import sqlite3

import pytest
from PIL import Image

from gideon.operations.durability import conflicts
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.jobs import MediaJobs
from gideon.workspace.capabilities.media.sketches import SketchStore
from gideon.workspace.capabilities.media.timelines import TimelineStore
from gideon.workspace.capabilities.platform import replication_video as adapter


def jobs(home):
    artifacts = NativeArtifactProvider(home / "artifacts")
    sketches = SketchStore(home / "capabilities/media/sketches.sqlite3", artifacts)
    return MediaJobs(home / "capabilities/media/jobs.sqlite3", sketches)


def image(store, color, slug):
    raw = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(raw, "PNG")
    return store.sketches.artifacts.create_binary(
        name=color, slug=slug, data=raw.getvalue(), mime="image/png"
    )


def seed(home, suffix="one", second=False):
    store = jobs(home)
    red = image(store, "red", "red-" + suffix)
    blue = image(store, "blue", "blue-" + suffix)
    body = {
        "title": "Sequence " + suffix,
        "width": 32,
        "height": 24,
        "fps": 4,
        "request_id": "timeline-" + suffix,
        "segments": [
            {
                "artifact_id": red.slug,
                "version": red.version,
                "kind": "image",
                "start": 0,
                "duration": 1,
            },
            {
                "artifact_id": blue.slug,
                "version": blue.version,
                "kind": "image",
                "start": 0,
                "duration": 1,
            },
        ],
        "overlays": [
            {
                "artifact_id": red.slug,
                "version": red.version,
                "start": 0,
                "duration": 1,
                "x": 0,
                "y": 0,
                "width": 8,
                "height": 8,
            }
        ],
        "audio": [],
    }
    timeline = store.timelines.save(body)
    if second:
        timeline = store.timelines.save(
            {
                **body,
                "title": "Sequence revised " + suffix,
                "revision": 1,
                "request_id": "timeline-revised-" + suffix,
            },
            timeline["id"],
        )
    return store, timeline, body


def materialize_pins(home, row):
    store = jobs(home)
    identities = {
        entry["artifact_id"]
        for key in ("segments", "overlays")
        for entry in row["data"][key]
    }
    for index, identity in enumerate(sorted(identities)):
        image(store, "red" if index == 0 else "blue", identity)
    return store


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def test_projection_is_current_only_with_exact_pins_and_local_authority(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    store, current, _ = seed(source, "current", second=True)
    projected = rows(source)
    assert projected == [{"id": current["id"], "data": current}]
    assert projected[0]["data"]["revision"] == 2
    adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": projected}])
    encoded = json.dumps(projected, sort_keys=True)
    assert (
        "request_id" not in encoded
        and "private" not in encoded
        and "events" not in encoded
    )
    with sqlite3.connect(store.timelines.path) as database:
        assert database.execute("SELECT count(*) FROM timelines").fetchone()[0] == 2
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 2
    applied = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        projected,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T12:00:00+00:00",
    )
    assert (
        applied.added == 1
        and applied.updated == applied.removed == applied.conflicts == 0
    )
    assert (
        TimelineStore(
            target / "capabilities/media/timelines.sqlite3", store.videos
        ).get(current["id"])
        == current
    )
    assert NativeArtifactProvider(target / "artifacts").list() == []
    with sqlite3.connect(target / "capabilities/media/timelines.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
    assert not (target / "capabilities/media/jobs.sqlite3").exists()


def test_two_home_actual_edit_conflict_restore_and_tombstone(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first_jobs, project, body = seed(first, "shared")
    baseline = rows(first)
    initial = adapter.apply_rows(
        second,
        adapter.ENTRY_ID,
        baseline,
        {},
        conflicts.ConflictQueue(second),
        "2026-09-25T12:05:00+00:00",
    )
    second_jobs = materialize_pins(second, baseline[0])
    local = first_jobs.timelines.save(
        {
            **body,
            "title": "Local title",
            "segments": list(reversed(body["segments"])),
            "revision": 1,
            "request_id": "local-edit",
        },
        project["id"],
    )
    peer = second_jobs.timelines.save(
        {**body, "title": "Peer title", "revision": 1, "request_id": "peer-edit"},
        project["id"],
    )
    queue = conflicts.ConflictQueue(first)
    outcome = adapter.apply_rows(
        first,
        adapter.ENTRY_ID,
        rows(second),
        initial.new_ancestors,
        queue,
        "2026-09-25T12:10:00+00:00",
    )
    assert outcome.conflicts == 1 and outcome.updated == 0
    assert first_jobs.timelines.get(project["id"]) == local
    conflict = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)[0]
    assert conflict.remote_row["data"] == peer
    adapter.restore_fields(first, conflict.id, ["title"], "2026-09-25T12:15:00+00:00")
    merged = first_jobs.timelines.get(project["id"])
    assert (
        merged["title"] == "Peer title"
        and merged["segments"] == local["segments"]
        and merged["revision"] == 3
    )
    for entry in merged["segments"]:
        assert first_jobs.sketches.artifacts.raw_bytes(
            entry["artifact_id"], version=entry["version"]
        )

    source, replica = tmp_path / "delete-source", tmp_path / "delete-replica"
    source_jobs, doomed, _ = seed(source, "doomed")
    copied = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        rows(source),
        {},
        conflicts.ConflictQueue(replica),
        "2026-09-25T12:20:00+00:00",
    )
    with source_jobs.timelines.db() as database:
        database.execute("DELETE FROM timelines WHERE id=?", (doomed["id"],))
    removed = adapter.apply_rows(
        replica,
        adapter.ENTRY_ID,
        [],
        copied.new_ancestors,
        conflicts.ConflictQueue(replica),
        "2026-09-25T12:25:00+00:00",
    )
    assert removed.removed == 1 and rows(replica) == []


def test_complete_batch_rejects_private_malformed_and_cross_project_dependencies(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    _, project, _ = seed(source, "validate")
    canonical = rows(source)[0]
    invalid = []
    value = copy.deepcopy(canonical)
    value["data"]["request_id"] = "private-request"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["segments"][0]["version"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["segments"][0]["kind"] = "document"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["duration"] = 99
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["overlays"][0]["artifact_id"] = "../secret"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["updated_at"] = "yesterday"
    invalid.append(value)
    for row in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [row]}])
    duplicate = [canonical, copy.deepcopy(canonical)]
    with pytest.raises(ValueError, match="unique"):
        adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": duplicate}])
    other = copy.deepcopy(canonical)
    other["id"] = "second-project"
    other["data"]["id"] = "second-project"
    other["data"]["segments"][0]["artifact_id"] = project["id"]
    with pytest.raises(ValueError, match="project identities as artifact dependencies"):
        adapter.validate_entries(
            [{"entry_id": adapter.ENTRY_ID, "rows": [canonical, other]}]
        )
    with pytest.raises(ValueError, match="complete entry coverage"):
        adapter.validate_entries([])
    with pytest.raises(ValueError):
        adapter.apply_rows(
            target,
            adapter.ENTRY_ID,
            [invalid[1]],
            {},
            conflicts.ConflictQueue(target),
            "2026-09-25T12:30:00+00:00",
        )
    assert rows(target) == []


def test_writer_preserves_pins_without_materializing_jobs_requests_or_bytes(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source_jobs, project, _ = seed(source, "writer")
    source_jobs.submit(
        {
            "operation": "timeline_render",
            "request_id": "private-render",
            "input": {"timeline_id": project["id"], "revision": project["revision"]},
        }
    )
    row = rows(source)[0]
    adapter.write_row(target, adapter.ENTRY_ID, row, project["id"])
    reopened = TimelineStore(
        target / "capabilities/media/timelines.sqlite3", source_jobs.videos
    )
    assert reopened.get(project["id"]) == project
    for key in ("segments", "overlays", "audio"):
        for ref in project[key]:
            assert (
                NativeArtifactProvider(target / "artifacts").raw_bytes(
                    ref["artifact_id"], version=ref["version"]
                )
                is None
            )
    with sqlite3.connect(target / "capabilities/media/timelines.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
    assert not (target / "capabilities/media/jobs.sqlite3").exists()
    adapter.write_row(target, adapter.ENTRY_ID, None, project["id"])
    assert rows(target) == []
