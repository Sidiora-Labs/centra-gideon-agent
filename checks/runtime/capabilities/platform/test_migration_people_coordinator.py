import base64
import hashlib
import io
import json
import tarfile

import pytest

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy, BoundTasks
from gideon.workspace.capabilities.platform import migration
from gideon.workspace.capabilities.platform.migration import (
    FORMAT,
    MigrationError,
    commit,
    preview,
    receipts,
)

PERSON_SOURCE = "person-coordinated"
PROJECT = "60606060-6060-4060-8060-606060606060"
ADMIN = "61616161-6161-4161-8161-616161616161"
THREAD = "thread-people-coordinated"


def records_archive(collections):
    files = {}
    for domain, rows in collections.items():
        files[f"brain/{domain}/index.json"] = json.dumps(
            {
                "schemaVersion": 1,
                "type": domain,
                "updatedAt": "2026-09-25T00:00:00Z",
                "config": {},
            }
        ).encode()
        files.update(
            {
                f"brain/{domain}/{row['id']}/index.json": json.dumps(row).encode()
                for row in rows
            }
        )
    manifest = {
        "generatedAt": "2026-09-25T00:00:00Z",
        "fileCount": len(files),
        "files": {
            name: hashlib.sha256(value).hexdigest() for name, value in files.items()
        },
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        payloads = {
            "snapshot-people/manifest.json": json.dumps(manifest).encode(),
            **{f"snapshot-people/data/{name}": value for name, value in files.items()},
        }
        for name, value in payloads.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    return {"format": FORMAT, "content": base64.b64encode(output.getvalue()).decode()}


def mixed_source(*, broken=False):
    person = {
        "id": PERSON_SOURCE,
        "name": "Ada Lovelace",
        "context": "Migration collaborator",
        "followUps": ["Review restore"],
        "tags": ["coordination"],
        "lastTouched": "2026-09-24T00:00:00Z",
        "createdAt": "2025-01-01T00:00:00Z",
        "updatedAt": "2026-09-24T00:00:00Z",
    }
    project = {
        "id": PROJECT,
        "name": "People restore",
        "status": "active",
        "nextAction": "Bind owner",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-09-25T00:00:00Z",
    }
    admin = {
        "id": ADMIN,
        "title": "Prepare people restore",
        "status": "open",
        "nextAction": "Publish records",
        "notes": "First task",
        "createdAt": "2026-02-01T00:00:00Z",
        "updatedAt": "2026-09-25T00:01:00Z",
    }
    refs = (
        [{"kind": "brain.person", "id": "missing-person", "label": "Missing"}]
        if broken
        else [
            {"kind": "brain.person", "id": PERSON_SOURCE, "label": "Ada Lovelace"},
            {"kind": "brain.project", "id": PROJECT, "label": "People restore"},
            {"kind": "brain.admin", "id": ADMIN, "label": "Prepare people restore"},
        ]
    )
    thread = {
        "id": THREAD,
        "title": "Verify person relation",
        "status": "open",
        "priority": "high",
        "nextAction": "Inspect canonical IDs",
        "notes": "Final task",
        "tags": [],
        "pinned": False,
        "refs": refs,
        "createdAt": "2026-02-02T00:00:00Z",
        "updatedAt": "2026-09-25T00:02:00Z",
    }
    return records_archive(
        {
            "people": [person],
            "projects": [project],
            "admin": [admin],
            "threads": [thread],
        }
    )


def opened(tmp_path):
    home = tmp_path / "home"
    return (
        home,
        PeopleStore(home / "capabilities" / "communications"),
        KnowledgeStore(str(home / "knowledge.db")),
        BoundHierarchy(home),
        BoundTasks(home),
    )


def reviewed(source):
    checked = preview(source)
    return checked, {
        **source,
        "archive_digest": checked["archive_digest"],
        "review_token": checked["review_token"],
    }


def test_people_project_tasks_share_schema_two_journal_refs_restart_and_replay(
    tmp_path,
):
    source = mixed_source()
    checked, payload = reviewed(source)
    home, people, knowledge, projects, tasks = opened(tmp_path)
    assert checked["coverage"]["supported"] == [
        "people",
        "projects",
        "admin",
        "threads",
    ]
    receipt, created = commit(people, payload, knowledge, projects, tasks)
    assert created is True and receipt["domains"] == {
        "admin": 1,
        "people": 1,
        "projects": 1,
        "threads": 1,
    }
    person_record = next(row for row in receipt["records"] if row["domain"] == "people")
    person = people.get(person_record["person_id"])
    assert (
        person_record["source_id"] == PERSON_SOURCE
        and person_record["revision"] == person["revision"] == 1
    )
    assert "Legacy record: " + PERSON_SOURCE in person["notes"]
    task = tasks._read_task(tasks._task_path(THREAD))
    assert [ref["canonical_id"] for ref in task.evidence[0]["references"]] == [
        person["id"],
        PROJECT,
        ADMIN,
    ]
    path = (
        migration._canonical_coordinator_root(tasks)
        / f'{receipt["archive_digest"]}.json'
    )
    journal_bytes = path.read_bytes()
    journal = json.loads(journal_bytes)
    assert journal["schema"] == 2 and journal["status"] == "complete"
    assert [step["adapter"] for step in journal["steps"]] == [
        "person",
        "project",
        "task",
        "task",
    ]
    reopened_people = PeopleStore(home / "capabilities" / "communications")
    assert receipts(
        reopened_people, knowledge, BoundHierarchy(home), BoundTasks(home)
    ) == [receipt]
    replay, replay_created = commit(
        reopened_people, payload, knowledge, BoundHierarchy(home), BoundTasks(home)
    )
    assert (
        replay_created is False
        and replay == receipt
        and path.read_bytes() == journal_bytes
    )
    knowledge.close()


def test_late_task_failure_compensates_owned_person_project_and_task(tmp_path):
    source = mixed_source(broken=True)
    _, payload = reviewed(source)
    _, people, knowledge, projects, tasks = opened(tmp_path)
    with pytest.raises(MigrationError, match="does not resolve uniquely"):
        commit(people, payload, knowledge, projects, tasks)
    assert people.people() == [] and projects.get_project(PROJECT) is None
    assert (
        not tasks._task_path(ADMIN).exists() and not tasks._task_path(THREAD).exists()
    )
    assert list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()


def test_same_archive_person_reference_prefers_stable_coordinator_identity(tmp_path):
    source = mixed_source()
    _, payload = reviewed(source)
    _, people, knowledge, projects, tasks = opened(tmp_path)
    prior = people.save(
        {
            "name": "Older Ada",
            "identities": [],
            "notes": "Legacy record: " + PERSON_SOURCE,
            "ring": "tribe",
            "cadence_days": 30,
        }
    )
    receipt, _ = commit(people, payload, knowledge, projects, tasks)
    person_id = next(
        row["person_id"] for row in receipt["records"] if row["domain"] == "people"
    )
    references = tasks._read_task(tasks._task_path(THREAD)).evidence[0]["references"]
    assert references[0]["canonical_id"] == person_id and person_id != prior["id"]
    assert people.get(prior["id"])["name"] == "Older Ada"
    knowledge.close()


def test_crash_after_person_write_before_checkpoint_resumes_owned_person(
    tmp_path, monkeypatch
):
    source = mixed_source()
    _, payload = reviewed(source)
    _, people, knowledge, projects, tasks = opened(tmp_path)
    original = migration._apply_canonical_step
    crashed = {"done": False}

    def crash_after_person(step, journal, *stores):
        original(step, journal, *stores)
        if step["adapter"] == "person" and not crashed["done"]:
            crashed["done"] = True
            raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(migration, "_apply_canonical_step", crash_after_person)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        commit(people, payload, knowledge, projects, tasks)
    assert len(people.people()) == 1 and projects.get_project(PROJECT) is None
    paths = list(migration._canonical_coordinator_root(tasks).glob("*.json"))
    assert (
        len(paths) == 1
        and json.loads(paths[0].read_text())["steps"][0]["state"] == "pending"
    )
    monkeypatch.setattr(migration, "_apply_canonical_step", original)
    restored = receipts(people, knowledge, projects, tasks)
    assert len(restored) == 1 and restored[0]["domains"]["people"] == 1
    assert (
        len(people.people()) == 1
        and projects.get_project(PROJECT) is not None
        and tasks._task_path(THREAD).exists()
    )
    knowledge.close()


def test_person_touchpoint_is_identity_drift_and_freezes_incomplete_plan(tmp_path):
    source = mixed_source()
    _, payload = reviewed(source)
    _, people, knowledge, projects, tasks = opened(tmp_path)
    receipt, _ = commit(people, payload, knowledge, projects, tasks)
    person_id = next(
        row["person_id"] for row in receipt["records"] if row["domain"] == "people"
    )
    people.record(
        person_id,
        {
            "source": "email",
            "external_id": "message-1",
            "occurred_at": "2026-09-25T00:03:00Z",
            "direction": "inbound",
            "summary": "User activity",
        },
    )
    path = (
        migration._canonical_coordinator_root(tasks)
        / f'{receipt["archive_digest"]}.json'
    )
    journal = json.loads(path.read_text())
    journal["status"] = "applying"
    journal["steps"][-1]["state"] = "pending"
    tasks._task_path(THREAD).unlink()
    migration._write_journal(path, journal)
    with pytest.raises(MigrationError, match="identity drift|compensation refused"):
        receipts(people, knowledge, projects, tasks)
    assert (
        people.get(person_id)["name"] == "Ada Lovelace"
        and people.touchpoints(person_id)[0]["external_id"] == "message-1"
    )
    assert (
        projects.get_project(PROJECT) is not None
        and tasks._task_path(ADMIN).exists()
        and path.exists()
    )
    knowledge.close()


def test_deterministic_person_collision_is_rejected_before_journal_or_other_writes(
    tmp_path,
):
    source = mixed_source()
    checked, payload = reviewed(source)
    _, people, knowledge, projects, tasks = opened(tmp_path)
    person_id = (
        "migration-"
        + hashlib.sha256(
            (checked["archive_digest"] + ":" + PERSON_SOURCE).encode()
        ).hexdigest()[:32]
    )
    body = {
        "id": person_id,
        "name": "Existing canonical person",
        "identities": [],
        "notes": "Preserve",
        "ring": "tribe",
        "cadence_days": 30,
    }
    with people.connect() as db:
        db.execute("INSERT INTO people VALUES (?,?,1)", (person_id, json.dumps(body)))
    with pytest.raises(MigrationError, match="target identity already exists"):
        commit(people, payload, knowledge, projects, tasks)
    assert (
        people.get(person_id)["name"] == "Existing canonical person"
        and projects.get_project(PROJECT) is None
    )
    assert (
        not tasks._task_path(ADMIN).exists()
        and list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    )
    knowledge.close()
