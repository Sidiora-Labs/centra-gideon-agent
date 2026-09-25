import copy
import io
import json
import sqlite3
import wave

import pytest
from PIL import Image

from gideon.operations.durability import conflicts
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.store import DomainError
from gideon.workspace.capabilities.music.video import VideoStore
from gideon.workspace.capabilities.platform import replication_music_video as adapter


def wav_bytes(seconds=3, rate=8000):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * seconds * rate)
    return output.getvalue()


def store_at(home):
    music = home / "capabilities" / "music"
    return VideoStore(
        music, MusicCatalog(music, NativeArtifactProvider(root=home / "artifacts"))
    )


def create_project(home, suffix="one"):
    store = store_at(home)
    track = store.catalog.create("tracks", {"title": "Signal " + suffix})
    audio = store.catalog.artifacts.create_binary(
        name="Actual recording " + suffix,
        data=wav_bytes(),
        mime="audio/wav",
        kind="audio",
        source="import",
    )
    track = store.catalog.attach(
        track["id"],
        {
            "revision": track["revision"],
            "artifact_ref": {"slug": audio.slug, "version": audio.version},
            "source": {
                "kind": "imported",
                "label": "Authored session",
                "license": "Test fixture",
            },
        },
    )
    images = []
    for color in ("red", "blue"):
        output = io.BytesIO()
        Image.new("RGB", (96, 64), color).save(output, format="PNG")
        artifact = store.catalog.artifacts.create_binary(
            name=f"{color} frame {suffix}",
            data=output.getvalue(),
            mime="image/png",
            kind="image",
            source="manual",
        )
        images.append({"slug": artifact.slug, "version": artifact.version})
    project = store.create(
        {
            "title": "Beat cut " + suffix,
            "track_id": track["id"],
            "render_id": track["renders"][0]["id"],
            "tempo_bpm": 120,
            "offset_seconds": 0.25,
            "scenes": [
                {"id": "opening", "image_ref": images[0], "beats": 1},
                {"id": "resolve", "image_ref": images[1], "beats": 2},
            ],
        }
    )
    return store, project, track, audio, images


def editable(project, **changes):
    result = {
        key: project[key]
        for key in (
            "title",
            "track_id",
            "render_id",
            "tempo_bpm",
            "offset_seconds",
            "revision",
        )
    }
    result["scenes"] = [
        {key: scene[key] for key in ("id", "image_ref", "beats")}
        for scene in project["scenes"]
    ]
    result.update(changes)
    return result


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def test_projection_preserves_pins_and_editable_grid_without_bytes_or_execution_authority(
    tmp_path,
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    store, project, track, audio, images = create_project(source, "projection")
    with store._db() as database:
        job = {
            "id": "local-render",
            "status": "completed",
            "project_id": project["id"],
            "revision": project["revision"],
            "snapshot": project,
            "artifact_ref": {"slug": "local-output", "version": 1},
            "error": None,
        }
        database.execute(
            "INSERT INTO jobs VALUES(?,?,?)",
            ("local-render", "private-request", json.dumps(job)),
        )
    projected = rows(source)
    assert projected == [{"id": project["id"], "data": project}]
    adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": projected}])
    record = projected[0]["data"]
    assert record["revision"] == 1
    assert record["track_id"] == track["id"]
    assert record["render_id"] == track["renders"][0]["id"]
    assert record["audio_ref"] == {"slug": audio.slug, "version": 1}
    assert [scene["image_ref"] for scene in record["scenes"]] == images
    assert [scene["start_seconds"] for scene in record["scenes"]] == [0.0, 0.5]
    assert [scene["duration_seconds"] for scene in record["scenes"]] == [0.5, 1.0]
    assert record["duration_seconds"] == 1.5
    wire = json.dumps(projected, sort_keys=True)
    assert "local-render" not in wire
    assert "private-request" not in wire
    assert "credential" not in wire
    assert "provider_config" not in wire
    assert "RIFF" not in wire
    outcome = adapter.apply_rows(
        target,
        adapter.ENTRY_ID,
        projected,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T12:00:00+00:00",
    )
    assert (outcome.added, outcome.updated, outcome.removed, outcome.conflicts) == (
        1,
        0,
        0,
        0,
    )
    assert store_at(target).get(project["id"]) == project
    with sqlite3.connect(target / "capabilities/music/music_video.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    target_store = store_at(target)
    with pytest.raises(DomainError) as unavailable:
        target_store.update(
            project["id"], editable(project, title="Needs local dependencies")
        )
    assert unavailable.value.status == 404
    assert target_store.catalog.list("tracks") == []
    assert target_store.catalog.artifacts.get(audio.slug, version=1) is None
    for image in images:
        assert (
            target_store.catalog.artifacts.get(image["slug"], version=image["version"])
            is None
        )
    assert target_store.get(project["id"]) == project


def test_two_home_fast_forward_conflict_selected_restore_and_tombstone(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_store, project, _, _, _ = create_project(first, "shared")
    initial_rows = rows(first)
    initial = adapter.apply_rows(
        second,
        adapter.ENTRY_ID,
        initial_rows,
        {},
        conflicts.ConflictQueue(second),
        "2026-09-25T13:00:00+00:00",
    )
    ancestor = initial.new_ancestors
    assert ancestor == {project["id"]: conflicts.row_sha(initial_rows[0])}
    changed = first_store.update(
        project["id"], editable(project, title="Local editorial cut")
    )
    forwarded = adapter.apply_rows(
        second,
        adapter.ENTRY_ID,
        rows(first),
        ancestor,
        conflicts.ConflictQueue(second),
        "2026-09-25T13:05:00+00:00",
    )
    assert (
        forwarded.added,
        forwarded.updated,
        forwarded.removed,
        forwarded.conflicts,
    ) == (0, 1, 0, 0)
    assert store_at(second).get(project["id"]) == changed
    replayed = adapter.apply_rows(
        second,
        adapter.ENTRY_ID,
        rows(first),
        forwarded.new_ancestors,
        conflicts.ConflictQueue(second),
        "2026-09-25T13:10:00+00:00",
    )
    assert (replayed.added, replayed.updated, replayed.removed, replayed.conflicts) == (
        0,
        0,
        0,
        0,
    )
    assert replayed.new_ancestors == forwarded.new_ancestors

    local = first_store.update(project["id"], editable(changed, title="Source title"))
    remote = copy.deepcopy(changed)
    remote.update(title="Peer title", revision=3)
    adapter.write_row(
        second, adapter.ENTRY_ID, {"id": project["id"], "data": remote}, project["id"]
    )
    queue = conflicts.ConflictQueue(first)
    conflicted = adapter.apply_rows(
        first,
        adapter.ENTRY_ID,
        rows(second),
        forwarded.new_ancestors,
        queue,
        "2026-09-25T13:15:00+00:00",
    )
    assert (
        conflicted.added,
        conflicted.updated,
        conflicted.removed,
        conflicted.conflicts,
    ) == (0, 0, 0, 1)
    assert first_store.get(project["id"]) == local
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1
    assert pending[0].local_row["data"] == local
    assert pending[0].remote_row["data"] == remote
    resolved = adapter.restore_fields(
        first, pending[0].id, ["title"], "2026-09-25T13:20:00+00:00"
    )
    assert resolved["fields"] == ["title"]
    merged = first_store.get(project["id"])
    assert merged["title"] == "Peer title"
    assert merged["scenes"] == local["scenes"]
    assert merged["revision"] == 4
    assert queue.get(pending[0].id).status == conflicts.STATUS_RESOLVED

    tombstone_source = tmp_path / "tombstone-source"
    tombstone_target = tmp_path / "tombstone-target"
    _, doomed, _, _, _ = create_project(tombstone_source, "doomed")
    baseline = rows(tombstone_source)
    copied = adapter.apply_rows(
        tombstone_target,
        adapter.ENTRY_ID,
        baseline,
        {},
        conflicts.ConflictQueue(tombstone_target),
        "2026-09-25T13:25:00+00:00",
    )
    adapter.write_row(tombstone_source, adapter.ENTRY_ID, None, doomed["id"])
    removed = adapter.apply_rows(
        tombstone_target,
        adapter.ENTRY_ID,
        [],
        copied.new_ancestors,
        conflicts.ConflictQueue(tombstone_target),
        "2026-09-25T13:30:00+00:00",
    )
    assert (removed.added, removed.updated, removed.removed, removed.conflicts) == (
        0,
        0,
        1,
        0,
    )
    assert rows(tombstone_target) == []
    with pytest.raises(DomainError) as missing:
        store_at(tombstone_target).get(doomed["id"])
    assert missing.value.status == 404


def test_complete_preflight_rejects_bad_pins_grids_private_fields_and_duplicates(
    tmp_path,
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _, _, _, _, _ = create_project(source, "validation")
    canonical = rows(source)[0]
    invalid = []
    value = copy.deepcopy(canonical)
    value["data"]["audio_ref"]["version"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["audio_ref"]["slug"] = ""
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"][0]["image_ref"]["version"] = True
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"][0]["start_seconds"] = 1
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"][1]["duration_seconds"] = 2
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["duration_seconds"] = 2
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["tempo_bpm"] = True
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["tempo_bpm"] = 301
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["offset_seconds"] = float("inf")
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"] = []
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"][1]["id"] = "opening"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"][0]["beats"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["revision"] = 0
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["request_id"] = "private"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["audio_ref"]["credential"] = "secret"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value["data"]["scenes"][0]["bytes"] = "png"
    invalid.append(value)
    for row in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [row]}])
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([])
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([{"entry_id": "music.other", "rows": [canonical]}])
    with pytest.raises(ValueError, match="Duplicate"):
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
            "2026-09-25T14:00:00+00:00",
        )
    assert rows(target) == []
    assert store_at(target).list() == []
    with sqlite3.connect(target / "capabilities/music/music_video.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_writer_only_materializes_current_project_row(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _, project, track, audio, images = create_project(source, "writer")
    row = rows(source)[0]
    adapter.write_row(target, adapter.ENTRY_ID, row, project["id"])
    reopened = store_at(target)
    assert reopened.get(project["id"]) == project
    assert reopened.list() == [project]
    assert reopened.catalog.list("tracks") == []
    assert reopened.catalog.artifacts.get(audio.slug, version=1) is None
    assert track["id"] == project["track_id"]
    assert track["renders"][0]["id"] == project["render_id"]
    assert track["renders"][0]["artifact_ref"] == project["audio_ref"]
    assert [scene["image_ref"] for scene in project["scenes"]] == images
    with sqlite3.connect(target / "capabilities/music/music_video.sqlite3") as database:
        stored = json.loads(
            database.execute(
                "SELECT payload FROM projects WHERE id=?", (project["id"],)
            ).fetchone()[0]
        )
        assert stored == project
        assert database.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    with pytest.raises(ValueError, match="Unknown"):
        adapter.read_rows(target, "music.other")
    with pytest.raises(ValueError, match="Unknown"):
        adapter.write_row(target, "music.other", row, project["id"])
    mismatched = copy.deepcopy(row)
    mismatched["id"] = "different-project"
    with pytest.raises(ValueError, match="identity"):
        adapter.write_row(target, adapter.ENTRY_ID, mismatched, project["id"])
    assert reopened.get(project["id"]) == project
    adapter.write_row(target, adapter.ENTRY_ID, None, project["id"])
    assert reopened.list() == []
