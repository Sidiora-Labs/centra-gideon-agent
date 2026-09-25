import base64
import hashlib
import io
import json
import tarfile

import pytest

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy, BoundTasks
from gideon.workspace.capabilities.music.store import RepertoireStore
from gideon.workspace.capabilities.platform import migration
from gideon.workspace.capabilities.platform.migration import (
    FORMAT,
    MigrationError,
    commit,
    preview,
)

PROJECT = "81818181-8181-4181-8181-818181818181"


def mixed_archive(*, include_inbox=False):
    now = "2026-09-25T10:00:00Z"
    attachment = b"%PDF-1.7\nmixed score\n"
    song = {
        "id": "song_mixed",
        "title": "Mixed Song",
        "artist": "Ada",
        "instrument": "guitar",
        "stage": "learning",
        "tags": ["mixed"],
        "key": "C",
        "capo": 0,
        "tuning": "EADGBE",
        "sourceUrl": "",
        "links": [],
        "content": {"format": "plain", "text": "C F G"},
        "notes": "",
        "scrollDurationSec": None,
        "attachments": [
            {
                "filename": "score.pdf",
                "label": "Score",
                "mime": "application/pdf",
                "size": len(attachment),
                "sha256": hashlib.sha256(attachment).hexdigest(),
            }
        ],
        "createdAt": now,
        "updatedAt": now,
    }
    person = {
        "id": "mixed-person",
        "name": "Ada",
        "context": "Grouped import",
        "followUps": [],
        "tags": [],
        "createdAt": now,
        "updatedAt": now,
    }
    project = {
        "id": PROJECT,
        "name": "Mixed restore",
        "status": "active",
        "nextAction": "Practice",
        "createdAt": now,
        "updatedAt": now,
    }
    files = {
        "brain/people/index.json": json.dumps(
            {"schemaVersion": 1, "type": "people"}
        ).encode(),
        "brain/people/mixed-person/index.json": json.dumps(person).encode(),
        "brain/projects/index.json": json.dumps(
            {"schemaVersion": 1, "type": "projects"}
        ).encode(),
        f"brain/projects/{PROJECT}/index.json": json.dumps(project).encode(),
        "brain/songs/index.json": json.dumps(
            {"schemaVersion": 1, "type": "songs"}
        ).encode(),
        "brain/songs/song_mixed/index.json": json.dumps(song).encode(),
        "brain/songbook/score.pdf": attachment,
    }
    if include_inbox:
        capture_id = "82828282-8282-4282-8282-828282828282"
        files["brain/inbox/index.json"] = json.dumps(
            {"schemaVersion": 1, "type": "inbox"}
        ).encode()
        files[f"brain/inbox/{capture_id}/index.json"] = json.dumps(
            {
                "id": capture_id,
                "capturedText": "Immutable grouped capture",
                "capturedAt": now,
                "source": "brain_ui",
                "status": "needs_review",
                "classification": {
                    "destination": "unknown",
                    "confidence": 0.4,
                    "title": "Grouped capture",
                    "extracted": {},
                },
            }
        ).encode()
    manifest = {
        "generatedAt": now,
        "fileCount": len(files),
        "files": {
            name: hashlib.sha256(value).hexdigest() for name, value in files.items()
        },
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, value in {
            "snapshot/manifest.json": json.dumps(manifest).encode(),
            **{f"snapshot/data/{name}": value for name, value in files.items()},
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return {
        "format": FORMAT,
        "content": base64.b64encode(stream.getvalue()).decode(),
    }, attachment


def opened(home):
    artifacts = NativeArtifactProvider(home / "artifacts")
    return (
        PeopleStore(home / "capabilities" / "communications"),
        KnowledgeStore(str(home / "knowledge.db")),
        BoundHierarchy(home),
        BoundTasks(home),
        RepertoireStore(home / "capabilities" / "music", artifacts),
        artifacts,
    )


def reviewed(source):
    checked = preview(source)
    return {
        **source,
        "archive_digest": checked["archive_digest"],
        "review_token": checked["review_token"],
    }


def test_mixed_song_canonical_groups_commit_restart_and_exact_replay(tmp_path):
    source, attachment = mixed_archive()
    inspected = preview(source)
    assert inspected["commit_groups"] == [
        {"id": "canonical", "domains": ["people", "projects"]},
        {"id": "song", "domains": ["songs"]},
    ]
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    receipt, created = commit(
        people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts
    )
    assert created is True and receipt["status"] == "complete"
    assert receipt["domains"] == {"people": 1, "projects": 1, "songs": 1}
    slug = receipt["records"][-1]["attachment_refs"][0]["slug"]
    assert artifacts.raw_bytes(slug, version=1)[0] == attachment
    path = migration._grouped_root(repertoire) / f'{receipt["archive_digest"]}.json'
    before = path.read_bytes()
    knowledge.close()
    restarted = opened(home)
    try:
        replay, replay_created = commit(restarted[0], reviewed(source), *restarted[1:])
        assert (
            replay_created is False
            and replay == receipt
            and path.read_bytes() == before
        )
    finally:
        restarted[1].close()


def test_song_failure_reports_partial_and_exact_retry_finishes_only_pending_group(
    tmp_path, monkeypatch
):
    source, _ = mixed_archive()
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    original = migration._commit_song
    monkeypatch.setattr(
        migration,
        "_commit_song",
        lambda *args: (_ for _ in ()).throw(MigrationError("GPU unavailable", 503)),
    )
    partial, created = commit(
        people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts
    )
    assert (
        created is True
        and partial["status"] == "partial"
        and partial["domains"] == {"people": 1, "projects": 1}
    )
    project_bytes = (projects._projects_dir() / PROJECT / "project.json").read_bytes()
    person = people.people()[0]
    knowledge.close()
    monkeypatch.setattr(migration, "_commit_song", original)
    restarted = opened(home)
    try:
        complete, retry_created = commit(restarted[0], reviewed(source), *restarted[1:])
        assert (
            retry_created is True
            and complete["status"] == "complete"
            and complete["domains"]["songs"] == 1
        )
        assert (
            restarted[2]._projects_dir() / PROJECT / "project.json"
        ).read_bytes() == project_bytes
        assert restarted[0].get(person["id"]) == person
    finally:
        restarted[1].close()


def test_completed_canonical_group_drift_refuses_song_retry_without_erasing_records(
    tmp_path, monkeypatch
):
    source, _ = mixed_archive()
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    original = migration._commit_song
    monkeypatch.setattr(
        migration,
        "_commit_song",
        lambda *args: (_ for _ in ()).throw(MigrationError("stopped", 503)),
    )
    partial, _ = commit(
        people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts
    )
    person_id = next(
        row["person_id"] for row in partial["records"] if row["domain"] == "people"
    )
    people.record(
        person_id,
        {
            "source": "email",
            "external_id": "after-partial",
            "occurred_at": "2026-09-25T11:00:00Z",
            "direction": "inbound",
            "summary": "User changed canonical state",
        },
    )
    monkeypatch.setattr(migration, "_commit_song", original)
    with pytest.raises(MigrationError, match="identity drift"):
        commit(
            people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts
        )
    assert repertoire.list() == [] and projects.get_project(PROJECT) is not None
    assert people.touchpoints(person_id)[0]["external_id"] == "after-partial"
    knowledge.close()


def test_full_plan_collision_rejects_before_people_or_project_mutation(tmp_path):
    source, _ = mixed_archive()
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    slug = "legacy-song-" + hashlib.sha256(b"song_mixed:score.pdf").hexdigest()[:32]
    artifacts.create_binary(
        name="Collision",
        data=b"different",
        mime="application/pdf",
        kind="pdf",
        slug=slug,
    )
    with pytest.raises(MigrationError, match="different bytes"):
        commit(
            people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts
        )
    assert people.people() == [] and projects.get_project(PROJECT) is None
    assert list(migration._grouped_root(repertoire).glob("*.json")) == []
    knowledge.close()


def test_ambiguous_attachment_tree_rejects_before_people_or_project_mutation(tmp_path):
    source, _ = mixed_archive()
    home = tmp_path / "home"
    people, knowledge, projects, tasks, repertoire, artifacts = opened(home)
    slug = "legacy-song-" + hashlib.sha256(b"song_mixed:score.pdf").hexdigest()[:32]
    external = tmp_path / "external-artifact"
    external.mkdir()
    (external / "meta.json").write_text('{"user":"owned"}')
    artifacts.root.mkdir(parents=True, exist_ok=True)
    (artifacts.root / slug).symlink_to(external, target_is_directory=True)
    with pytest.raises(MigrationError, match="ambiguous filesystem state") as caught:
        commit(
            people, reviewed(source), knowledge, projects, tasks, repertoire, artifacts
        )
    assert caught.value.status == 409
    assert people.people() == [] and projects.get_project(PROJECT) is None
    assert tasks._all_tasks() == []
    assert list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    assert (artifacts.root / slug).is_symlink() and (
        external / "meta.json"
    ).read_text() == '{"user":"owned"}'
    assert list(migration._grouped_root(repertoire).glob("*.json")) == []
    knowledge.close()
