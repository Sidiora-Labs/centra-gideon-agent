import base64
import hashlib
import io
import json
import tarfile
from copy import deepcopy
from contextlib import closing

import pytest

from gideon.engine.tasks.models import Task
from gideon.engine.tasks.native import TaskMutation
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy, BoundTasks
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.store import RepertoireStore
from gideon.workspace.capabilities.platform import migration
from gideon.workspace.capabilities.platform.migration import FORMAT, MigrationError, commit, preview, receipts


def archive(records=None, *, extra=None, schema=1, mutate=None, manifest_patch=None, root="snapshot-1"):
    records = records if records is not None else [{"id": "person-1", "name": "Ada", "context": "Research collaborator", "followUps": ["Send notes"], "tags": ["work"], "lastTouched": "2026-09-01T00:00:00.000Z", "createdAt": "2026-01-01T00:00:00.000Z", "updatedAt": "2026-09-01T00:00:00.000Z"}]
    files = {"brain/people/index.json": json.dumps({"schemaVersion": schema, "type": "people", "updatedAt": "2026-09-01T00:00:00.000Z", "config": {}}).encode()}
    files.update({f"brain/people/{row['id']}/index.json": json.dumps(row).encode() for row in records})
    files.update(extra or {})
    manifest = {"generatedAt": "2026-09-12T00:00:00.000Z", "fileCount": len(files), "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    if manifest_patch:
        manifest_patch(manifest)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        payloads = {f"{root}/manifest.json": json.dumps(manifest).encode(), **{f"{root}/data/{name}": value for name, value in files.items()}}
        if mutate:
            mutate(tar, payloads)
        else:
            for name, value in payloads.items():
                info = tarfile.TarInfo(name); info.size = len(value); tar.addfile(info, io.BytesIO(value))
    return {"format": FORMAT, "content": base64.b64encode(output.getvalue()).decode()}


def knowledge_archive(memories=None, links=None, *, schema=1):
    files = {}
    if memories is not None:
        files["brain/memories/index.json"] = json.dumps({"schemaVersion": schema, "type": "memories", "updatedAt": "2026-09-12T00:00:00Z", "config": {}}).encode()
        files.update({f"brain/memories/{row['id']}/index.json": json.dumps(row).encode() for row in memories})
    if links is not None:
        files["brain/links/index.json"] = json.dumps({"schemaVersion": schema, "type": "links", "updatedAt": "2026-09-12T00:00:00Z", "config": {}}).encode()
        files.update({f"brain/links/{row['id']}/index.json": json.dumps(row).encode() for row in links})
    manifest = {"generatedAt": "2026-09-12T00:00:00.000Z", "fileCount": len(files), "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, value in {"snapshot-k/manifest.json": json.dumps(manifest).encode(), **{f"snapshot-k/data/{name}": value for name, value in files.items()}}.items():
            info = tarfile.TarInfo(name); info.size = len(value); tar.addfile(info, io.BytesIO(value))
    return {"format": FORMAT, "content": base64.b64encode(output.getvalue()).decode()}


def domains_archive(collections, *, schema=1):
    files = {}
    for domain, rows in collections.items():
        files[f"brain/{domain}/index.json"] = json.dumps({"schemaVersion": schema, "type": domain, "updatedAt": "2026-09-12T00:00:00Z", "config": {}}).encode()
        files.update({f"brain/{domain}/{row['id']}/index.json": json.dumps(row).encode() for row in rows})
    manifest = {"generatedAt": "2026-09-12T00:00:00.000Z", "fileCount": len(files), "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, value in {"snapshot-domains/manifest.json": json.dumps(manifest).encode(), **{f"snapshot-domains/data/{name}": value for name, value in files.items()}}.items():
            info = tarfile.TarInfo(name); info.size = len(value); tar.addfile(info, io.BytesIO(value))
    return {"format": FORMAT, "content": base64.b64encode(output.getvalue()).decode()}


def song_archive(song, binaries=None, *, schema=1):
    files = {
        "brain/songs/index.json": json.dumps({"schemaVersion": schema, "type": "songs", "updatedAt": "2026-09-25T00:00:00Z", "config": {}}).encode(),
        f"brain/songs/{song['id']}/index.json": json.dumps(song).encode(),
        **{f"brain/songbook/{name}": value for name, value in (binaries or {}).items()},
    }
    manifest = {"generatedAt": "2026-09-25T00:00:00.000Z", "fileCount": len(files),
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, value in {"snapshot-song/manifest.json": json.dumps(manifest).encode(),
                            **{f"snapshot-song/data/{name}": value for name, value in files.items()}}.items():
            info = tarfile.TarInfo(name); info.size = len(value); tar.addfile(info, io.BytesIO(value))
    return {"format": FORMAT, "content": base64.b64encode(output.getvalue()).decode()}


def song_record(identity="song-1", attachments=None, **overrides):
    record = {
        "id": identity, "title": "Midnight Train", "artist": "The Testers", "instrument": "guitar",
        "stage": "learning", "tags": ["setlist", "acoustic"], "key": "Am", "capo": 2,
        "tuning": "E A D G B E", "sourceUrl": "https://example.test/song", "links": [
            {"type": "project", "id": "tour-set", "label": "Tour set"}],
        "content": {"format": "chordpro", "text": "{title: Midnight Train}\n[Am]All aboard"},
        "notes": "Keep the bridge quiet", "scrollDurationSec": 90, "attachments": attachments or [],
        "practice": {"ease": 2.7, "intervalDays": 6, "nextReview": "2026-10-01T12:00:00+00:00",
                     "lastReviewed": "2026-09-25T12:00:00+00:00", "sessions": 4, "lastQuality": 4},
        "createdAt": "2026-01-01T00:00:00+00:00", "updatedAt": "2026-09-25T12:00:00+00:00",
    }
    record.update(overrides)
    return record


def project_task_records(project_id="40404040-4040-4040-8040-404040404040", task_id="thread-mixed", refs=None):
    project = {"id": project_id, "name": "Mixed restore", "status": "active", "nextAction": "Ship it",
               "notes": "Coordinated project", "tags": ["migration"],
               "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z"}
    thread = {"id": task_id, "title": "Coordinate restore", "status": "open", "priority": "high",
              "nextAction": "Verify canonical IDs", "notes": "Coordinated task", "tags": ["migration"], "pinned": False,
              "refs": refs if refs is not None else [{"kind": "brain.project", "id": project_id, "label": "Mixed restore"}],
              "createdAt": "2026-02-02T00:00:00Z", "updatedAt": "2026-02-03T00:00:00Z"}
    return project, thread


def multi_canonical_archive(*, broken_ref=False):
    project_one = {"id": "50505050-5050-4050-8050-505050505050", "name": "Alpha restore", "status": "active",
                   "nextAction": "Coordinate", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z"}
    project_two = {"id": "51515151-5151-4151-8151-515151515151", "name": "Beta restore", "status": "waiting",
                   "nextAction": "Link records", "createdAt": "2026-01-02T00:00:00Z", "updatedAt": "2026-02-02T00:00:00Z"}
    admin = {"id": "52525252-5252-4252-8252-525252525252", "title": "Prepare restore", "status": "open",
             "nextAction": "Publish projects", "notes": "First task", "createdAt": "2026-02-03T00:00:00Z",
             "updatedAt": "2026-02-04T00:00:00Z"}
    first_thread = {"id": "thread-alpha", "title": "Link alpha", "status": "open", "priority": "high",
                    "nextAction": "Verify links", "notes": "Depends on project and admin", "tags": [], "pinned": False,
                    "refs": [{"kind": "brain.project", "id": project_one["id"], "label": "Alpha restore"},
                             {"kind": "brain.admin", "id": admin["id"], "label": "Prepare restore"}],
                    "createdAt": "2026-02-05T00:00:00Z", "updatedAt": "2026-02-06T00:00:00Z"}
    final_refs = ([{"kind": "brain.person", "id": "missing-person", "label": "Missing"}] if broken_ref else
                  [{"kind": "brain.project", "id": project_two["id"], "label": "Beta restore"},
                   {"kind": "cos.task", "id": first_thread["id"], "label": "Link alpha"}])
    final_thread = {"id": "thread-beta", "title": "Link beta", "status": "open", "priority": "normal",
                    "nextAction": "Finish", "notes": "Final task", "tags": [], "pinned": False, "refs": final_refs,
                    "createdAt": "2026-02-07T00:00:00Z", "updatedAt": "2026-02-08T00:00:00Z"}
    source = domains_archive({"projects": [project_two, project_one], "admin": [admin],
                              "threads": [final_thread, first_thread]})
    return source, (project_one, project_two), (admin, first_thread, final_thread)


def test_preview_verifies_manifest_and_maps_explicit_coverage():
    result = preview(archive())
    assert result["format"] == FORMAT
    assert result["coverage"] == {"supported": ["people"], "unsupported": "all other snapshot domains"}
    assert result["records"] == [{"source_id": "person-1", "domain": "people", "name": "Ada"}]
    assert len(result["archive_digest"]) == len(result["review_token"]) == 64


def test_commit_is_atomic_persistent_and_idempotent(tmp_path):
    store = PeopleStore(tmp_path)
    source = archive()
    checked = preview(source)
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    first, created = commit(store, payload)
    assert created is True
    assert first["domains"] == {"people": 1}
    assert store.people()[0]["name"] == "Ada"
    assert store.people()[0]["notes"] == "Research collaborator\nFollow-ups: Send notes\nTags: work\nLast touched: 2026-09-01T00:00:00.000Z\nLegacy record: person-1"
    second, created = commit(PeopleStore(tmp_path), payload)
    assert created is False
    assert second == first
    assert len(PeopleStore(tmp_path).people()) == 1
    assert receipts(PeopleStore(tmp_path)) == [first]


def test_changed_archive_after_preview_leaves_store_empty(tmp_path):
    store = PeopleStore(tmp_path)
    source = archive()
    checked = preview(source)
    changed = archive([{"id": "person-2", "name": "Grace", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}])
    with pytest.raises(MigrationError, match="changed since preview"):
        commit(store, {**changed, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]})
    assert store.people() == []
    assert receipts(store) == []


def test_two_records_commit_in_one_canonical_transaction(tmp_path):
    rows = [{"id": "one", "name": "One", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}, {"id": "two", "name": "Two", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}]
    source = archive(rows)
    checked = preview(source)
    receipt, created = commit(PeopleStore(tmp_path), {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]})
    assert created and receipt["domains"]["people"] == 2
    assert [row["name"] for row in PeopleStore(tmp_path).people()] == ["Two", "One"] or sorted(row["name"] for row in PeopleStore(tmp_path).people()) == ["One", "Two"]


@pytest.mark.parametrize("source,message", [
    ({"format": "future", "content": "eA=="}, "format"),
    ({"format": FORMAT, "content": "not-base64"}, "base64"),
    ({"format": FORMAT, "content": base64.b64encode(b"not-tar").decode()}, "gzip tar"),
])
def test_invalid_containers_fail_closed(source, message):
    with pytest.raises(MigrationError, match=message):
        preview(source)


def test_checksum_mismatch_fails_before_store_write(tmp_path):
    source = archive(manifest_patch=lambda value: value["files"].update({"brain/people/person-1/index.json": "0" * 64}))
    with pytest.raises(MigrationError, match="Checksum mismatch"):
        preview(source)
    assert PeopleStore(tmp_path).people() == []


def test_unmanifested_file_fails_closed():
    def add(_tar, payloads):
        payloads["snapshot-1/data/brain/people/extra/index.json"] = b"{}"
        for name, value in payloads.items():
            info = tarfile.TarInfo(name); info.size = len(value); _tar.addfile(info, io.BytesIO(value))
    with pytest.raises(MigrationError, match="inventories differ"):
        preview(archive(mutate=add))


def test_missing_manifest_fails_closed():
    def omit(_tar, payloads):
        for name, value in payloads.items():
            if name.endswith("manifest.json"): continue
            info = tarfile.TarInfo(name); info.size = len(value); _tar.addfile(info, io.BytesIO(value))
    with pytest.raises(MigrationError, match="manifest is required"):
        preview(archive(mutate=omit))


def test_link_entry_is_never_extracted_or_followed():
    def link(_tar, payloads):
        for name, value in payloads.items():
            info = tarfile.TarInfo(name); info.size = len(value); _tar.addfile(info, io.BytesIO(value))
        info = tarfile.TarInfo("snapshot-1/data/link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"; _tar.addfile(info)
    with pytest.raises(MigrationError, match="links"):
        preview(archive(mutate=link))


def test_traversal_entry_is_rejected():
    def traversal(_tar, payloads):
        for name, value in payloads.items():
            info = tarfile.TarInfo(name); info.size = len(value); _tar.addfile(info, io.BytesIO(value))
        value = b"escape"; info = tarfile.TarInfo("snapshot-1/../escape"); info.size = len(value); _tar.addfile(info, io.BytesIO(value))
    with pytest.raises(MigrationError, match="unsafe path"):
        preview(archive(mutate=traversal))


def test_more_than_one_snapshot_root_is_rejected():
    def two_roots(_tar, payloads):
        for name, value in payloads.items():
            info = tarfile.TarInfo(name); info.size = len(value); _tar.addfile(info, io.BytesIO(value))
        value = b"x"; info = tarfile.TarInfo("other/file"); info.size = 1; _tar.addfile(info, io.BytesIO(value))
    with pytest.raises(MigrationError, match="one snapshot"):
        preview(archive(mutate=two_roots))


@pytest.mark.parametrize("extra,domain", [
    ({"brain/future/admin/index.json": b"{}"}, "brain"),
    ({"media/item.bin": b"media"}, "media"),
    ({"../legacy-db.sql": b"select 1"}, "unsafe path"),
])
def test_unsupported_domains_and_database_dump_fail_closed(extra, domain):
    with pytest.raises(MigrationError, match=domain):
        preview(archive(extra=extra))


@pytest.mark.parametrize("schema", [0, 2, "1"])
def test_unknown_people_schema_versions_fail_closed(schema):
    with pytest.raises(MigrationError, match="schema version"):
        preview(archive(schema=schema))


def test_unknown_record_fields_fail_closed():
    row = {"id": "person-1", "name": "Ada", "secretFutureField": True, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}
    with pytest.raises(MigrationError, match="unsupported fields"):
        preview(archive([row]))


def test_tombstones_fail_closed_instead_of_resurrecting_deleted_people():
    with pytest.raises(MigrationError, match="tombstone"):
        preview(archive([{"id": "person-1", "_deleted": True, "updatedAt": "2026-01-01T00:00:00Z"}]))


def test_record_directory_and_body_identity_must_match():
    row = {"id": "body-id", "name": "Ada", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}
    source = archive([row])
    raw = base64.b64decode(source["content"])
    assert raw
    bad = deepcopy(row); bad["id"] = "different"
    with pytest.raises(MigrationError, match="identity"):
        preview(archive([], extra={"brain/people/body-id/index.json": json.dumps(bad).encode()}))


def test_invalid_canonical_name_fails_during_preview():
    row = {"id": "person-1", "name": "", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}
    with pytest.raises(ValueError, match="name is required"):
        preview(archive([row]))


def test_preexisting_deterministic_target_rolls_back_receipt_and_all_new_rows(tmp_path):
    store = PeopleStore(tmp_path)
    source = archive()
    checked = preview(source)
    target = "migration-" + hashlib.sha256((checked["archive_digest"] + ":person-1").encode()).hexdigest()[:32]
    with closing(store.connect()) as db, db:
        body = {"id": target, "name": "Existing", "identities": [], "notes": "", "ring": "tribe", "cadence_days": 30}
        db.execute("INSERT INTO people VALUES (?,?,?)", (target, json.dumps(body), 1))
    with pytest.raises(MigrationError, match="target identity"):
        commit(store, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]})
    assert [row["name"] for row in store.people()] == ["Existing"]
    assert receipts(store) == []


def test_memory_and_link_preview_and_atomic_canonical_commit(tmp_path):
    memory_id = "11111111-1111-4111-8111-111111111111"
    link_id = "22222222-2222-4222-8222-222222222222"
    source = knowledge_archive(
        memories=[{"id": memory_id, "title": "A durable evening", "content": "Rain and neon", "mood": "reflective", "tags": ["city", "memory"], "source": "conversation-import", "sourceRef": "conversation-7.json", "sourceCreatedAt": "2025-01-02T03:04:05Z", "sourceUpdatedAt": "2025-01-03T03:04:05Z", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}],
        links=[{"id": link_id, "url": "https://Example.com/docs/?utm_source=old", "title": "Reference docs", "description": "Primary reference", "note": "Read chapter two", "linkType": "documentation", "tags": ["docs"], "isRepo": False, "createdAt": "2025-02-01T00:00:00Z", "updatedAt": "2025-02-02T00:00:00Z"}],
    )
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["memories", "links"]
    assert checked["records"] == [{"source_id": link_id, "domain": "links", "name": "Reference docs"}, {"source_id": memory_id, "domain": "memories", "name": "A durable evening"}]
    people = PeopleStore(tmp_path / "people")
    knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert created is True
    assert receipt["domains"] == {"links": 1, "memories": 1}
    memory = knowledge.get_item(memory_id)
    assert (memory["item_type"], memory["title"], memory["content"], memory["created_at"], memory["updated_at"]) == ("note", "A durable evening", "Rain and neon", "2025-01-02T03:04:05Z", "2025-01-03T03:04:05Z")
    assert memory["provider"] == "legacy-migration" and memory["source_id"] == "legacy-archive" and memory["guid"] == f"memories:{memory_id}"
    assert memory["tags"] == ["city", "memory"]
    assert memory["file_metadata"]["source_ref"] == "conversation-7.json"
    link = knowledge.get_item(link_id)
    assert link["item_type"] == "bookmark" and link["url"] == "https://example.com/docs/"
    assert link["content"] == "Primary reference\nRead chapter two" and link["tags"] == ["docs"]
    again, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert created is False and again == receipt
    assert receipts(people, knowledge) == [receipt]


def test_knowledge_collision_rolls_back_every_record_and_receipt(tmp_path):
    first = "33333333-3333-4333-8333-333333333333"; second = "44444444-4444-4444-8444-444444444444"
    rows = [{"id": first, "title": "First", "content": "one", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}, {"id": second, "title": "Second", "content": "two", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}]
    source = knowledge_archive(memories=rows)
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    knowledge.create_typed_item(item_type="note", title="Existing", content="same id")
    knowledge.db.execute("UPDATE items SET id=? WHERE title='Existing'", (second,)); knowledge.db.commit()
    with pytest.raises(MigrationError, match="target identity"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert knowledge.get_item(first) is None
    assert receipts(people, knowledge) == []


def test_people_and_knowledge_must_be_separate_atomic_imports():
    memory_id = "55555555-5555-4555-8555-555555555555"
    extra = {"brain/memories/index.json": json.dumps({"schemaVersion": 1, "type": "memories"}).encode(), "brain/memories/" + memory_id + "/index.json": json.dumps({"id": memory_id, "title": "Memory", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}).encode()}
    with pytest.raises(MigrationError, match="separate atomic imports"):
        preview(archive(extra=extra))


def test_unknown_memory_fields_versions_and_tombstones_fail_closed():
    identity = "66666666-6666-4666-8666-666666666666"
    with pytest.raises(MigrationError, match="unsupported fields"):
        preview(knowledge_archive(memories=[{"id": identity, "title": "Memory", "future": True, "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}]))
    with pytest.raises(MigrationError, match="schema version"):
        preview(knowledge_archive(memories=[{"id": identity, "title": "Memory", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}], schema=2))
    with pytest.raises(MigrationError, match="tombstone"):
        preview(knowledge_archive(memories=[{"id": identity, "_deleted": True, "updatedAt": "2025-01-01T00:00:00Z"}]))


def test_memory_content_is_stored_as_text_and_never_executed_as_sql(tmp_path):
    identity = "77777777-7777-4777-8777-777777777777"
    source = knowledge_archive(memories=[{"id": identity, "title": "Literal SQL", "content": "DROP TABLE items;", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}])
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert knowledge.get_item(identity)["content"] == "DROP TABLE items;"
    assert knowledge.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_ideas_and_journals_share_one_atomic_knowledge_commit(tmp_path):
    idea_id = "99999999-9999-4999-8999-999999999999"
    journal_id = "2026-09-20"
    source = domains_archive({
        "ideas": [{"id": idea_id, "title": "Build a solar kiln", "status": "done", "oneLiner": "Dry timber with sunlight", "notes": "Prototype used reclaimed glass", "tags": ["making"], "createdAt": "2025-05-01T00:00:00Z", "updatedAt": "2025-06-01T00:00:00Z"}],
        "journals": [{"id": journal_id, "date": journal_id, "content": "Shipped the first prototype.", "segments": [{"text": "Shipped the first prototype.", "at": "2026-09-20T18:00:00Z", "source": "voice"}], "createdAt": "2026-09-20T18:00:00Z", "updatedAt": "2026-09-20T18:00:00Z"}],
    })
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["ideas", "journals"]
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert created and receipt["domains"] == {"ideas": 1, "journals": 1}
    idea = knowledge.get_item(idea_id)
    assert idea["item_type"] == "fleeting" and idea["is_archived"] is True
    assert idea["content"] == "Dry timber with sunlight\nPrototype used reclaimed glass" and idea["tags"] == ["making"]
    journal = knowledge.get_item(journal_id)
    assert journal["item_type"] == "journal" and journal["guid"].startswith("date_journal:")
    assert journal["file_metadata"]["journal_date"] == journal_id
    assert journal["file_metadata"]["segments"][0]["source"] == "voice"


def test_idea_collision_rolls_back_journal_and_receipt(tmp_path):
    idea_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"; day = "2026-09-21"
    source = domains_archive({"ideas": [{"id": idea_id, "title": "Collision", "status": "active", "oneLiner": "Existing", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}], "journals": [{"id": day, "date": day, "content": "Must roll back", "segments": [], "createdAt": "2026-09-21T00:00:00Z", "updatedAt": "2026-09-21T00:00:00Z"}]})
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    knowledge.create_typed_item(item_type="fleeting", title="Existing", content="collision")
    knowledge.db.execute("UPDATE items SET id=? WHERE title='Existing'", (idea_id,)); knowledge.db.commit()
    with pytest.raises(MigrationError, match="target identity"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert knowledge.get_item(day) is None and receipts(people, knowledge) == []


def test_single_project_is_atomically_published_with_replay_receipt(tmp_path):
    project_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    source = domains_archive({"projects": [{"id": project_id, "name": "Solar kiln", "status": "blocked", "nextAction": "Source firebrick", "notes": "Waiting on supplier", "tags": ["woodshop"], "createdAt": "2025-03-01T00:00:00Z", "updatedAt": "2025-04-01T00:00:00Z"}]})
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db")); projects = BoundHierarchy(tmp_path / "home")
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects)
    assert created and receipt["domains"] == {"projects": 1}
    project = projects.get_project(project_id)
    assert project.name == "Solar kiln" and project.status == "active"
    assert project.created_at == "2025-03-01T00:00:00Z"
    assert project.brief == "Waiting on supplier\nNext action: Source firebrick\nLegacy status: blocked\nTags: woodshop"
    assert (tmp_path / "home/projects" / project_id / "context").is_dir()
    again, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects)
    assert created is False and again == receipt
    assert receipts(people, knowledge, projects) == [receipt]


def test_project_archive_refuses_multiple_records_and_mixed_store_domains(tmp_path):
    def project(identity, name):
        return {"id": identity, "name": name, "status": "active", "nextAction": "Continue", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}
    source = domains_archive({"projects": [project("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "One"), project("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "Two")]})
    checked = preview(source)
    with pytest.raises(MigrationError, match="exactly one project"):
        commit(PeopleStore(tmp_path / "people"), {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, KnowledgeStore(str(tmp_path / "knowledge.db")), BoundHierarchy(tmp_path / "home"))
    mixed = domains_archive({"projects": [project("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "Mixed")], "ideas": [{"id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "title": "Idea", "status": "active", "oneLiner": "Idea", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}]})
    with pytest.raises(MigrationError, match="separate atomic imports"):
        preview(mixed)


def test_project_and_thread_commit_with_same_archive_reference_and_exact_replay(tmp_path):
    project, thread = project_task_records()
    source = domains_archive({"projects": [project], "threads": [thread]})
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["projects", "threads"]
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    receipt, created = commit(people, payload, knowledge, projects, tasks)
    assert created is True and receipt["domains"] == {"projects": 1, "threads": 1}
    assert projects.get_project(project["id"]).name == "Mixed restore"
    restored = tasks._read_task(tasks._task_path(thread["id"]))
    assert restored.evidence[0]["references"] == [{"kind": "brain.project", "id": project["id"],
                                                     "label": "Mixed restore", "canonical_id": project["id"]}]
    replay, replay_created = commit(people, payload, knowledge, projects, tasks)
    assert replay_created is False and replay == receipt
    assert receipts(people, knowledge, projects, tasks) == [receipt]
    knowledge.close()


def _crashed_project_task_restore(tmp_path, *, edit_project=False):
    project, thread = project_task_records(project_id="41414141-4141-4141-8141-414141414141", task_id="thread-restart")
    source = domains_archive({"projects": [project], "threads": [thread]})
    digest, token, records, generated, _ = migration._inspect(source)
    receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
               "committed_at": "2026-09-25T12:00:00+00:00", "domains": {"projects": 1, "threads": 1},
               "records": [{"source_id": project["id"], "project_id": project["id"], "domain": "projects"},
                           {"source_id": thread["id"], "task_id": thread["id"], "domain": "threads"}]}
    journal = {"schema": 1, "kind": "project_task", "status": "prepared", "archive_digest": digest,
               "review_token": token, "generated_at": generated, "records": records,
               "records_hash": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(), "receipt": receipt}
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    path = migration._coordinator_root(tasks) / f"{digest}.json"
    migration._write_journal(path, journal)
    migration._commit_project(projects, digest, token, [row for row in records if row["domain"] == "projects"],
                              generated, receipt, require_fingerprint=True)
    if edit_project:
        project_path = home / "projects" / project["id"] / "project.json"
        body = json.loads(project_path.read_text())
        body["name"] = "Edited after interrupted restore"
        project_path.write_text(json.dumps(body))
    return source, receipt, people, knowledge, projects, tasks, path, project, thread


def test_restart_rolls_forward_project_ready_journal_and_deduplicates_receipt(tmp_path):
    source, receipt, people, knowledge, projects, tasks, path, project, thread = _crashed_project_task_restore(tmp_path)
    assert not tasks._task_path(thread["id"]).exists()
    assert receipts(people, knowledge, BoundHierarchy(projects.home), BoundTasks(tasks.home)) == [receipt]
    assert BoundTasks(tasks.home)._task_path(thread["id"]).exists()
    assert json.loads(path.read_text())["status"] == "complete"
    checked = preview(source)
    replay, created = commit(people, {**source, "archive_digest": checked["archive_digest"],
                              "review_token": checked["review_token"]}, knowledge, projects, tasks)
    assert created is False and replay == receipt
    knowledge.close()


def test_caught_failure_after_project_publication_compensates_owned_project(tmp_path):
    project, thread = project_task_records(refs=[{"kind": "brain.person", "id": "missing-person", "label": "Missing"}])
    source = domains_archive({"projects": [project], "threads": [thread]})
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    with pytest.raises(MigrationError, match="does not resolve uniquely"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
               knowledge, projects, tasks)
    assert projects.get_project(project["id"]) is None and not tasks._task_path(thread["id"]).exists()
    assert list(migration._coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()


def test_restart_refuses_compensation_after_owned_project_was_edited(tmp_path):
    _, _, people, knowledge, projects, tasks, path, project, thread = _crashed_project_task_restore(tmp_path, edit_project=True)
    with pytest.raises(MigrationError, match="automatic compensation refused"):
        receipts(people, knowledge, projects, tasks)
    assert projects.get_project(project["id"]).name == "Edited after interrupted restore"
    assert not tasks._task_path(thread["id"]).exists() and path.exists()
    knowledge.close()


def test_completed_coordinator_refuses_task_drift_and_preserves_journal(tmp_path):
    project, thread = project_task_records(project_id="46464646-4646-4646-8646-464646464646", task_id="thread-drift")
    source = domains_archive({"projects": [project], "threads": [thread]})
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
           knowledge, projects, tasks)
    task_path = tasks._task_path(thread["id"])
    body = json.loads(task_path.read_text()); body["title"] = "Edited after completion"; task_path.write_text(json.dumps(body))
    journal = migration._canonical_coordinator_root(tasks) / f"{checked['archive_digest']}.json"
    with pytest.raises(MigrationError, match="canonical migration has identity drift"):
        receipts(people, knowledge, projects, tasks)
    assert json.loads(task_path.read_text())["title"] == "Edited after completion" and journal.exists()
    knowledge.close()


def test_coordinator_journal_record_hash_and_size_are_bounded(tmp_path):
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    root = migration._coordinator_root(tasks)
    changed = root / ("a" * 64 + ".json")
    changed.write_text(json.dumps({"schema": 1, "kind": "project_task", "status": "prepared",
        "archive_digest": "a" * 64, "review_token": "b" * 64, "generated_at": "2026-01-01T00:00:00Z",
        "records": [], "records_hash": "c" * 64, "receipt": {"archive_digest": "a" * 64}}))
    with pytest.raises(MigrationError, match="record plan changed"):
        receipts(people, knowledge, projects, tasks)
    changed.unlink()
    oversized = root / ("d" * 64 + ".json")
    oversized.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(MigrationError, match="exceeds 2 MiB"):
        receipts(people, knowledge, projects, tasks)
    assert oversized.exists()
    knowledge.close()


def test_coordinator_target_collision_is_rejected_before_any_publication(tmp_path):
    project, thread = project_task_records()
    source = domains_archive({"projects": [project], "threads": [thread]})
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    tasks._write_task(Task(id=thread["id"], title="Existing task"))
    with pytest.raises(MigrationError, match="target identity already exists"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
               knowledge, projects, tasks)
    assert projects.get_project(project["id"]) is None
    assert tasks._read_task(tasks._task_path(thread["id"])).title == "Existing task"
    assert list(migration._coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()


def test_standalone_legacy_markers_without_fingerprint_replay_when_body_is_exact(tmp_path):
    project_id = "42424242-4242-4242-8242-424242424242"
    source = domains_archive({"projects": [{"id": project_id, "name": "Legacy marker", "status": "active",
        "nextAction": "Replay", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}]})
    checked = preview(source)
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    projects = BoundHierarchy(tmp_path / "home")
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    receipt, _ = commit(people, payload, knowledge, projects)
    marker = tmp_path / "home" / "projects" / project_id / ".migration-receipt.json"
    legacy = json.loads(marker.read_text()); legacy.pop("record_fingerprint"); marker.write_text(json.dumps(legacy))
    assert commit(people, payload, knowledge, projects) == (receipt, False)
    task_id = "43434343-4343-4343-8343-434343434343"
    task_source = domains_archive({"admin": [{"id": task_id, "title": "Legacy task marker", "status": "open",
        "nextAction": "Replay", "notes": "", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}]})
    task_checked = preview(task_source); tasks = BoundTasks(tmp_path / "home")
    task_payload = {**task_source, "archive_digest": task_checked["archive_digest"], "review_token": task_checked["review_token"]}
    task_receipt, _ = commit(people, task_payload, knowledge, projects, tasks)
    task_path = tasks._task_path(task_id)
    task_body = json.loads(task_path.read_text())
    task_body["evidence"][0].pop("record_fingerprint")
    task_path.write_text(json.dumps(task_body))
    assert commit(people, task_payload, knowledge, projects, tasks) == (task_receipt, False)
    knowledge.close()


def test_common_coordinator_imports_multiple_projects_admin_and_ordered_threads(tmp_path):
    source, project_rows, task_rows = multi_canonical_archive()
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["projects", "admin", "threads"]
    assert len(checked["records"]) == 5
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    receipt, created = commit(people, payload, knowledge, projects, tasks)
    assert created is True and receipt["domains"] == {"admin": 1, "projects": 2, "threads": 2}
    assert [projects.get_project(row["id"]).name for row in project_rows] == ["Alpha restore", "Beta restore"]
    admin, first_thread, final_thread = task_rows
    first = tasks._read_task(tasks._task_path(first_thread["id"]))
    final = tasks._read_task(tasks._task_path(final_thread["id"]))
    assert [ref["canonical_id"] for ref in first.evidence[0]["references"]] == [project_rows[0]["id"], admin["id"]]
    assert [ref["canonical_id"] for ref in final.evidence[0]["references"]] == [project_rows[1]["id"], first_thread["id"]]
    journal_path = migration._canonical_coordinator_root(tasks) / f"{checked['archive_digest']}.json"
    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "complete"
    assert [step["key"] for step in journal["steps"]] == [
        f"projects:{project_rows[0]['id']}", f"projects:{project_rows[1]['id']}", f"admin:{admin['id']}",
        f"threads:{first_thread['id']}", f"threads:{final_thread['id']}"]
    assert all(step["state"] == "applied" for step in journal["steps"])
    replay, replay_created = commit(people, payload, knowledge, BoundHierarchy(home), BoundTasks(home))
    assert replay_created is False and replay == receipt
    assert receipts(people, knowledge, BoundHierarchy(home), BoundTasks(home)) == [receipt]
    knowledge.close()


def test_common_coordinator_restart_resumes_each_pending_step_in_order(tmp_path):
    source, project_rows, task_rows = multi_canonical_archive()
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    receipt, _ = commit(people, payload, knowledge, projects, tasks)
    final_id = task_rows[-1]["id"]
    assert TaskMutation(tasks).delete(final_id)
    path = migration._canonical_coordinator_root(tasks) / f"{checked['archive_digest']}.json"
    journal = json.loads(path.read_text())
    journal["status"] = "applying"; journal["steps"][-1]["state"] = "pending"
    migration._write_journal(path, journal)
    reopened_tasks = BoundTasks(home)
    assert receipts(people, knowledge, BoundHierarchy(home), reopened_tasks) == [receipt]
    restored = reopened_tasks._read_task(reopened_tasks._task_path(final_id))
    assert restored.evidence[0]["references"][1]["canonical_id"] == task_rows[1]["id"]
    assert json.loads(path.read_text())["status"] == "complete"
    knowledge.close()


def test_common_coordinator_restart_adopts_owned_record_written_before_step_checkpoint(tmp_path):
    source, _, task_rows = multi_canonical_archive()
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    receipt, _ = commit(people, payload, knowledge, projects, tasks)
    path = migration._canonical_coordinator_root(tasks) / f"{checked['archive_digest']}.json"
    journal = json.loads(path.read_text())
    final_step = journal["steps"][-1]
    assert final_step["record"]["values"]["id"] == task_rows[-1]["id"]
    final_step["state"] = "pending"; journal["status"] = "applying"
    migration._write_journal(path, journal)
    before = tasks._task_path(task_rows[-1]["id"]).read_bytes()
    assert receipts(people, knowledge, BoundHierarchy(home), BoundTasks(home)) == [receipt]
    assert tasks._task_path(task_rows[-1]["id"]).read_bytes() == before
    assert json.loads(path.read_text())["steps"][-1]["state"] == "applied"
    knowledge.close()


def test_common_coordinator_compensates_all_owned_records_in_reverse_on_late_failure(tmp_path):
    source, project_rows, task_rows = multi_canonical_archive(broken_ref=True)
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    with pytest.raises(MigrationError, match="does not resolve uniquely"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
               knowledge, projects, tasks)
    assert all(projects.get_project(row["id"]) is None for row in project_rows)
    assert all(not tasks._task_path(row["id"]).exists() for row in task_rows)
    assert list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()


def test_common_coordinator_preserves_all_records_and_journal_when_one_applied_target_drifts(tmp_path):
    source, project_rows, task_rows = multi_canonical_archive()
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    receipt, _ = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
                        knowledge, projects, tasks)
    path = migration._canonical_coordinator_root(tasks) / f"{checked['archive_digest']}.json"
    journal = json.loads(path.read_text()); journal["status"] = "applying"; journal["steps"][-1]["state"] = "pending"
    assert TaskMutation(tasks).delete(task_rows[-1]["id"])
    first_path = tasks._task_path(task_rows[1]["id"])
    changed = json.loads(first_path.read_text()); changed["title"] = "User changed title"; first_path.write_text(json.dumps(changed))
    migration._write_journal(path, journal)
    with pytest.raises(MigrationError, match="identity drift|compensation refused"):
        receipts(people, knowledge, projects, tasks)
    assert all(projects.get_project(row["id"]) is not None for row in project_rows)
    assert tasks._task_path(task_rows[0]["id"]).exists() and first_path.exists() and path.exists()
    assert receipt["domains"]["threads"] == 2
    knowledge.close()


def test_common_coordinator_rejects_cycles_and_unrelated_cross_store_families():
    source, projects, tasks = multi_canonical_archive()
    data = {"projects": list(projects), "admin": [tasks[0]], "threads": [
        {**tasks[1], "refs": [{"kind": "cos.task", "id": tasks[2]["id"], "label": "Beta"}]},
        {**tasks[2], "refs": [{"kind": "cos.task", "id": tasks[1]["id"], "label": "Alpha"}]},
    ]}
    with pytest.raises(MigrationError, match="cycle"):
        preview(domains_archive(data))
    song = song_record("song-mixed")
    with pytest.raises(MigrationError, match="separate atomic imports"):
        preview(domains_archive({"projects": [projects[0]], "songs": [song]}))


def test_common_coordinator_supports_multiple_task_families_without_a_project(tmp_path):
    _, _, task_rows = multi_canonical_archive()
    admin, first_thread, final_thread = task_rows
    source = domains_archive({"admin": [admin], "threads": [first_thread, final_thread]})
    checked = preview(source)
    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    with pytest.raises(MigrationError, match="missing canonical project"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
               knowledge, projects, tasks)
    assert all(not tasks._task_path(row["id"]).exists() for row in task_rows)
    assert list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    no_project_refs = [{**first_thread, "refs": [{"kind": "brain.admin", "id": admin["id"], "label": "Admin"}]},
                       {**final_thread, "refs": [{"kind": "cos.task", "id": first_thread["id"], "label": "First"}]}]
    valid = domains_archive({"admin": [admin], "threads": no_project_refs})
    reviewed = preview(valid)
    receipt, created = commit(people, {**valid, "archive_digest": reviewed["archive_digest"],
                               "review_token": reviewed["review_token"]}, knowledge, projects, tasks)
    assert created and receipt["domains"] == {"admin": 1, "threads": 2}
    assert tasks._read_task(tasks._task_path(final_thread["id"])).evidence[0]["references"][0]["canonical_id"] == first_thread["id"]
    knowledge.close()


def test_common_coordinator_preflights_duplicate_project_names_before_journaling(tmp_path):
    source, project_rows, task_rows = multi_canonical_archive()
    duplicate = {**project_rows[1], "name": project_rows[0]["name"]}
    collision = domains_archive({"projects": [project_rows[0], duplicate], "admin": [task_rows[0]]})
    checked = preview(collision)
    home = tmp_path / "home"
    people = PeopleStore(home / "people"); knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    with pytest.raises(MigrationError, match="target identity already exists"):
        commit(people, {**collision, "archive_digest": checked["archive_digest"],
               "review_token": checked["review_token"]}, knowledge, projects, tasks)
    assert all(projects.get_project(row["id"]) is None for row in project_rows)
    assert not tasks._task_path(task_rows[0]["id"]).exists()
    assert list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()


def test_common_coordinator_rejects_duplicate_targets_and_more_than_one_hundred_records(tmp_path):
    _, project_rows, task_rows = multi_canonical_archive()
    duplicate_task_id = task_rows[0]["id"]
    duplicate_thread = {**task_rows[1], "id": duplicate_task_id, "refs": []}
    source = domains_archive({"admin": [task_rows[0]], "threads": [duplicate_thread]})
    with pytest.raises(MigrationError, match="duplicate canonical identity"):
        preview(source)
    projects = []
    for index in range(100):
        identity = f"{index:08x}-0000-4000-8000-{index:012x}"
        projects.append({"id": identity, "name": f"Project {index}", "status": "active", "nextAction": "Continue",
                         "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"})
    too_many = domains_archive({"projects": projects, "admin": [task_rows[0]]})
    with pytest.raises(MigrationError, match="between 2 and 100"):
        preview(too_many)
    home = tmp_path / "home"
    assert list(migration._canonical_coordinator_root(BoundTasks(home)).glob("*.json")) == []


def test_common_coordinator_journal_rejects_tampered_step_and_oversized_bytes(tmp_path):
    home = tmp_path / "home"
    people = PeopleStore(home / "people"); knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    root = migration._canonical_coordinator_root(tasks)
    bad = root / ("a" * 64 + ".json")
    bad_receipt = {"archive_digest": "a" * 64}
    bad.write_text(json.dumps({"schema": 2, "kind": "canonical_records", "status": "prepared",
        "archive_digest": "a" * 64, "review_token": "b" * 64, "generated_at": "2026-01-01T00:00:00Z",
        "steps": [{"key": "projects:x", "adapter": "project", "domain": "projects", "state": "pending",
                   "record": {"domain": "projects"}, "record_hash": "c" * 64},
                  {"key": "admin:y", "adapter": "task", "domain": "admin", "state": "pending",
                   "record": {"domain": "admin"}, "record_hash": "d" * 64}],
        "receipt": bad_receipt, "receipt_hash": hashlib.sha256(json.dumps(bad_receipt, sort_keys=True).encode()).hexdigest()}))
    with pytest.raises(MigrationError, match="unsupported step"):
        receipts(people, knowledge, projects, tasks)
    bad.unlink()
    huge = root / ("e" * 64 + ".json"); huge.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(MigrationError, match="exceeds 2 MiB"):
        receipts(people, knowledge, projects, tasks)
    assert huge.exists()
    knowledge.close()


def test_journal_date_segments_and_idea_fields_fail_closed():
    with pytest.raises(MigrationError, match="ISO date"):
        preview(domains_archive({"journals": [{"id": "not-a-day", "date": "not-a-day", "content": "x", "segments": [], "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}]}))
    with pytest.raises(MigrationError, match="segments"):
        preview(domains_archive({"journals": [{"id": "2026-09-22", "date": "2026-09-22", "content": "x", "segments": [{"text": "x", "at": "now", "source": "unknown"}], "createdAt": "2026-09-22T00:00:00Z", "updatedAt": "2026-09-22T00:00:00Z"}]}))
    with pytest.raises(MigrationError, match="one-liner"):
        preview(domains_archive({"ideas": [{"id": "12121212-1212-4212-8212-121212121212", "title": "Missing", "status": "active", "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}]}))


def test_buckets_and_links_import_as_canonical_collection_membership(tmp_path):
    bucket_id = "14141414-1414-4414-8414-141414141414"
    link_id = "15151515-1515-4515-8515-151515151515"
    source = domains_archive({
        "buckets": [{"id": bucket_id, "name": "Research", "color": "purple", "icon": "books", "order": 3,
                     "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-02-01T00:00:00Z"}],
        "links": [{"id": link_id, "title": "Reference", "url": "https://example.com/reference", "description": "Source",
                   "bucketId": bucket_id, "bucketOrder": 7, "createdAt": "2025-01-02T00:00:00Z", "updatedAt": "2025-02-02T00:00:00Z"}],
    })
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["links", "buckets"]
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert created and receipt["domains"] == {"buckets": 1, "links": 1}
    collection = dict(knowledge.db.execute("SELECT * FROM collections WHERE id=?", (bucket_id,)).fetchone())
    assert (collection["name"], collection["kind"], collection["icon"], collection["position"]) == ("Research", "manual", "books", 3)
    assert tuple(knowledge.db.execute("SELECT collection_id,item_id FROM collection_items").fetchone()) == (bucket_id, link_id)
    metadata = knowledge.get_item(link_id)["file_metadata"]
    assert (metadata["bucket_id"], metadata["bucket_order"]) == (bucket_id, 7)
    again, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert not created and again == receipt


def test_missing_bucket_reference_rolls_back_link_and_receipt(tmp_path):
    link_id = "16161616-1616-4616-8616-161616161616"
    source = domains_archive({"links": [{"id": link_id, "title": "Orphan", "url": "https://example.com/orphan",
        "bucketId": "17171717-1717-4717-8717-171717171717", "bucketOrder": 0,
        "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-01T00:00:00Z"}]})
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    with pytest.raises(MigrationError, match="missing canonical bucket"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert knowledge.get_item(link_id) is None
    assert knowledge.db.execute("SELECT count(*) FROM collection_items").fetchone()[0] == 0
    assert receipts(people, knowledge) == []


def test_inbox_state_and_history_import_with_same_transaction_destination(tmp_path):
    idea_id = "18181818-1818-4818-8818-181818181818"
    capture_id = "19191919-1919-4919-8919-191919191919"
    source = domains_archive({
        "ideas": [{"id": idea_id, "title": "Solar notes", "status": "active", "oneLiner": "Build a collector",
                   "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2025-01-02T00:00:00Z"}],
        "inbox": [{"id": capture_id, "capturedText": "Build a solar collector", "capturedAt": "2025-01-01T00:00:00Z",
                   "source": "brain_ui", "status": "corrected", "creative": True,
                   "classification": {"destination": "projects", "confidence": 0.6, "title": "Solar", "extracted": {}},
                   "filed": {"destination": "ideas", "destinationId": idea_id},
                   "correction": {"correctedAt": "2025-01-02T00:00:00Z", "previousDestination": "projects", "newDestination": "ideas"}}],
    })
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert created and receipt["domains"] == {"ideas": 1, "inbox": 1}
    capture = dict(knowledge.db.execute("SELECT * FROM capability_knowledge_captures WHERE id=?", (capture_id,)).fetchone())
    assert (capture["original_text"], capture["status"], capture["destination_id"]) == ("Build a solar collector", "routed", idea_id)
    event = knowledge.db.execute("SELECT event,payload,happened_at FROM capability_knowledge_capture_events WHERE capture_id=?", (capture_id,)).fetchone()
    assert event["event"] == "migration_snapshot" and json.loads(event["payload"])["creative"] is True
    assert event["happened_at"] == "2025-01-01T00:00:00Z"


def test_inbox_rejects_unverifiable_route_and_collision_rolls_back(tmp_path):
    capture_id = "20202020-2020-4020-8020-202020202020"
    bad = domains_archive({"inbox": [{"id": capture_id, "capturedText": "Call Ada", "capturedAt": "2025-01-01T00:00:00Z",
        "source": "brain_ui", "status": "filed", "filed": {"destination": "people", "destinationId": "21212121-2121-4121-8121-212121212121"}}]})
    with pytest.raises(MigrationError, match="supported canonical destination"):
        preview(bad)
    source = domains_archive({"inbox": [{"id": capture_id, "capturedText": "Review later", "capturedAt": "2025-01-01T00:00:00Z",
        "source": "brain_ui", "status": "needs_review"}]})
    checked = preview(source); people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    from gideon.workspace.capabilities.knowledge.capture import CaptureInbox
    CaptureInbox(knowledge)
    knowledge.db.execute("INSERT INTO capability_knowledge_captures (id,request_id,input_origin,original_text,captured_at,status,revision) VALUES (?,?,?,?,?,?,1)",
                         (capture_id, "existing-request", "text", "Existing", "2025-01-01T00:00:00Z", "needs_review"))
    knowledge.db.commit()
    with pytest.raises(MigrationError, match="target identity"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge)
    assert knowledge.db.execute("SELECT count(*) FROM capability_knowledge_capture_events").fetchone()[0] == 0
    assert receipts(people, knowledge) == []


def test_admin_import_is_one_crash_safe_canonical_task_with_embedded_receipt(tmp_path):
    identity = "22222222-2222-4222-8222-222222222222"
    source = domains_archive({"admin": [{"id": identity, "title": "Renew insurance", "status": "waiting",
        "dueDate": "2026-10-01T09:00:00Z", "nextAction": "Ask broker for revised terms", "notes": "Policy expires soon",
        "createdAt": "2026-08-01T10:00:00Z", "updatedAt": "2026-09-01T10:00:00Z"}]})
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["admin"]
    assert checked["records"] == [{"source_id": identity, "domain": "admin", "name": "Renew insurance"}]
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    projects = BoundHierarchy(tmp_path / "home"); tasks = BoundTasks(tmp_path / "home")
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects, tasks)
    assert created and receipt["domains"] == {"admin": 1}
    task = tasks._all_tasks()[0]
    assert task.id == identity and task.title == "Renew insurance" and task.status.value == "blocked"
    assert task.description == "Policy expires soon" and task.due == "2026-10-01T09:00:00Z"
    assert task.action_plan[0]["content"] == "Ask broker for revised terms" and not task.action_plan[0]["completed"]
    marker = task.evidence[0]
    assert marker["type"] == "platform_migration" and marker["archive_digest"] == checked["archive_digest"]
    assert marker["receipt"] == receipt and marker["source_record_id"] == identity
    assert not list((tmp_path / "home/tasks").glob(".migration-*"))
    again, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects, tasks)
    assert not created and again == receipt
    assert receipts(people, knowledge, projects, tasks) == [receipt]


def test_thread_import_preserves_work_state_and_resolves_real_references(tmp_path):
    idea_id = "23232323-2323-4323-8323-232323232323"
    thread_id = "24242424-2424-4424-8424-242424242424"
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    projects = BoundHierarchy(tmp_path / "home"); tasks = BoundTasks(tmp_path / "home")
    idea_source = domains_archive({"ideas": [{"id": idea_id, "title": "Solar kiln", "status": "active", "oneLiner": "Dry lumber",
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z"}]})
    idea_preview = preview(idea_source)
    commit(people, {**idea_source, "archive_digest": idea_preview["archive_digest"], "review_token": idea_preview["review_token"]}, knowledge, projects, tasks)
    source = domains_archive({"threads": [{"id": thread_id, "title": "Finish workshop plan", "status": "someday", "priority": "urgent",
        "nextAction": "Price firebrick", "notes": "Resume after the roof repair", "waitingOn": "Dry weather",
        "dueAt": "2026-12-01T00:00:00Z", "tags": ["workshop", "planning"], "pinned": True,
        "refs": [{"kind": "brain.idea", "id": idea_id, "label": "Solar kiln"},
                 {"kind": "url", "id": "https://example.com/firebrick", "label": "Supplier"}],
        "source": {"kind": "github.issue", "key": "example/workshop#1"}, "externalState": "open",
        "createdAt": "2026-02-01T00:00:00Z", "updatedAt": "2026-03-01T00:00:00Z"}]})
    checked = preview(source)
    receipt, created = commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects, tasks)
    assert created and receipt["domains"] == {"threads": 1}
    task = tasks._read_task(tasks._task_path(thread_id))
    assert task.status.value == "open" and task.priority.value == "critical"
    assert task.labels == ["workshop", "planning", "someday", "pinned"]
    assert task.notes == [{"content": "Waiting on: Dry weather", "timestamp": "2026-03-01T00:00:00Z"}]
    assert task.action_plan[0]["content"] == "Price firebrick" and task.due == "2026-12-01T00:00:00Z"
    refs = task.evidence[0]["references"]
    assert refs == [{"kind": "brain.idea", "id": idea_id, "label": "Solar kiln", "canonical_id": idea_id},
                    {"kind": "url", "id": "https://example.com/firebrick", "label": "Supplier", "canonical_id": "https://example.com/firebrick"}]
    assert task.evidence[0]["source_state"] == {"source": {"kind": "github.issue", "key": "example/workshop#1"}, "externalState": "open"}


def test_thread_person_reference_resolves_prior_migration_identity(tmp_path):
    source_person = "25252525-2525-4525-8525-252525252525"
    thread_id = "26262626-2626-4626-8626-262626262626"
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    projects = BoundHierarchy(tmp_path / "home"); tasks = BoundTasks(tmp_path / "home")
    person_source = archive([{"id": source_person, "name": "Ada", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}])
    person_preview = preview(person_source)
    person_receipt, _ = commit(people, {**person_source, "archive_digest": person_preview["archive_digest"], "review_token": person_preview["review_token"]}, knowledge, projects, tasks)
    canonical_person = person_receipt["records"][0]["person_id"]
    source = domains_archive({"threads": [{"id": thread_id, "title": "Ask Ada", "status": "open", "priority": "normal",
        "nextAction": "Send questions", "notes": "", "tags": [], "pinned": False,
        "refs": [{"kind": "brain.person", "id": source_person, "label": "Ada"}],
        "createdAt": "2026-02-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z"}]})
    checked = preview(source)
    commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects, tasks)
    task = tasks._read_task(tasks._task_path(thread_id))
    assert task.evidence[0]["references"][0]["canonical_id"] == canonical_person


def test_missing_thread_reference_fails_before_task_publication(tmp_path):
    identity = "27272727-2727-4727-8727-272727272727"
    source = domains_archive({"threads": [{"id": identity, "title": "Broken relation", "status": "open", "priority": "high",
        "nextAction": "Inspect", "notes": "", "tags": [], "pinned": False,
        "refs": [{"kind": "brain.project", "id": "28282828-2828-4828-8828-282828282828", "label": "Missing"}],
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}]})
    checked = preview(source)
    people = PeopleStore(tmp_path / "people"); knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    projects = BoundHierarchy(tmp_path / "home"); tasks = BoundTasks(tmp_path / "home")
    with pytest.raises(MigrationError, match="missing canonical project"):
        commit(people, {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, knowledge, projects, tasks)
    assert not tasks._task_path(identity).exists()
    assert receipts(people, knowledge, projects, tasks) == []


def test_task_archives_reject_multiple_records_and_cross_store_mixes(tmp_path):
    def admin(identity, title):
        return {"id": identity, "title": title, "status": "open", "nextAction": "Continue", "notes": "",
                "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}
    source = domains_archive({"admin": [admin("29292929-2929-4929-8929-292929292929", "One"), admin("30303030-3030-4030-8030-303030303030", "Two")]})
    checked = preview(source)
    with pytest.raises(MigrationError, match="exactly one record"):
        commit(PeopleStore(tmp_path / "people"), {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]},
               KnowledgeStore(str(tmp_path / "knowledge.db")), BoundHierarchy(tmp_path / "home"), BoundTasks(tmp_path / "home"))
    mixed = domains_archive({"admin": [admin("31313131-3131-4131-8131-313131313131", "Mixed")],
        "ideas": [{"id": "32323232-3232-4232-8232-323232323232", "title": "Idea", "status": "active", "oneLiner": "Idea",
                   "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}]})
    with pytest.raises(MigrationError, match="separate atomic imports"):
        preview(mixed)


def test_threads_fail_closed_for_unknown_refs_invalid_urls_and_future_fields():
    base = {"id": "33333333-3333-4333-8333-333333333333", "title": "Validate", "status": "open", "priority": "normal",
            "nextAction": "Check", "notes": "", "tags": [], "pinned": False,
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}
    for ref in ({"kind": "brain.song", "id": "song", "label": "Unsupported"},
                {"kind": "url", "id": "javascript:alert(1)", "label": "Unsafe"}):
        with pytest.raises(MigrationError, match="reference"):
            preview(domains_archive({"threads": [{**base, "refs": [ref]}]}))
    with pytest.raises(MigrationError, match="unsupported"):
        preview(domains_archive({"threads": [{**base, "refs": [], "future": True}]}))


def test_song_restore_materializes_real_artifacts_and_survives_restart(tmp_path):
    score, chart = b"%PDF-1.7\nreal score", b"# Chart\nAm C G"
    attachments = [
        {"filename": "score.pdf", "label": "Lead sheet", "mime": "application/pdf", "size": len(score),
         "sha256": hashlib.sha256(score).hexdigest()},
        {"filename": "chart.md", "label": "Working chart", "mime": "text/markdown", "size": len(chart),
         "sha256": hashlib.sha256(chart).hexdigest()},
    ]
    source = song_archive(song_record(attachments=attachments), {"score.pdf": score, "chart.md": chart})
    checked = preview(source)
    assert checked["coverage"]["supported"] == ["songs"]
    assert checked["records"] == [{"source_id": "song-1", "domain": "songs", "name": "Midnight Train"}]

    home = tmp_path / "home"
    people = PeopleStore(home / "capabilities" / "communications")
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    payload = {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}
    receipt, created = commit(people, payload, repertoire=repertoire, artifacts=artifacts)
    assert created is True and receipt["domains"] == {"songs": 1}
    item = repertoire.get("song-1")
    assert {key: item[key] for key in ("title", "artist", "instrument", "key", "capo", "tuning")} == {
        "title": "Midnight Train", "artist": "The Testers", "instrument": "guitar", "key": "Am", "capo": 2,
        "tuning": "E A D G B E"}
    assert item["notation"] == {"format": "chordpro", "text": "{title: Midnight Train}\n[Am]All aboard"}
    assert item["body"] == "Keep the bridge quiet" and item["tags"] == ["setlist", "acoustic"]
    assert item["stage"] == "learning" and item["interval"] == 6 and item["repetitions"] == 4
    assert item["due_at"] == "2026-10-01T12:00:00+00:00" and item["last_grade"] == 4
    assert all(row["available"] for row in item["attachment_availability"])
    pdf_ref, markdown_ref = item["attachment_refs"]
    assert artifacts.raw_bytes(pdf_ref["slug"], version=1) == (score, "application/pdf")
    assert artifacts.get(markdown_ref["slug"], version=1).content == chart.decode()

    replayed, replay_created = commit(people, payload, repertoire=repertoire, artifacts=artifacts)
    assert replay_created is False and replayed == receipt
    reopened_artifacts = NativeArtifactProvider(home / "artifacts")
    reopened_repertoire = RepertoireStore(home / "capabilities" / "music", reopened_artifacts)
    assert reopened_repertoire.get("song-1")["attachment_refs"] == item["attachment_refs"]
    assert receipts(people, repertoire=reopened_repertoire, artifacts=reopened_artifacts) == [receipt]


@pytest.mark.parametrize("mutation,message", [
    (lambda meta, blobs: blobs.clear(), "bytes are missing"),
    (lambda meta, blobs: meta.update(size=meta["size"] + 1), "size or checksum"),
    (lambda meta, blobs: meta.update(sha256="0" * 64), "size or checksum"),
    (lambda meta, blobs: meta.update(mime="text/plain"), "type is unsupported"),
])
def test_song_preview_rejects_missing_or_mismatched_attachment_material(mutation, message):
    raw = b"%PDF-1.7\nsource"
    meta = {"filename": "score.pdf", "label": "Score", "mime": "application/pdf", "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}
    blobs = {"score.pdf": raw}
    mutation(meta, blobs)
    with pytest.raises(MigrationError, match=message):
        preview(song_archive(song_record(attachments=[meta]), blobs))


def test_song_preview_rejects_unsafe_unreferenced_and_invalid_text_assets():
    raw = b"not utf8 \xff"
    metadata = {"filename": "chart.md", "label": "Chart", "mime": "text/markdown", "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()}
    with pytest.raises(MigrationError, match="valid UTF-8"):
        preview(song_archive(song_record(attachments=[metadata]), {"chart.md": raw}))
    with pytest.raises(MigrationError, match="unreferenced"):
        preview(song_archive(song_record(attachments=[]), {"orphan.pdf": b"%PDF"}))
    with pytest.raises(MigrationError, match="unsafe path"):
        preview(archive(extra={"brain/songbook/../escape.pdf": b"%PDF"}))


@pytest.mark.parametrize("override,message", [
    ({"instrument": "theremin"}, "core fields"),
    ({"content": {"format": "future", "text": "notes"}}, "core fields"),
    ({"sourceUrl": "https://user:secret@example.test/song"}, "musical fields"),
    ({"practice": {"ease": 1.0, "intervalDays": 0, "nextReview": "2026-01-01T00:00:00Z",
                   "lastReviewed": "2026-01-01T00:00:00Z", "sessions": 0, "lastQuality": 0}}, "practice history"),
])
def test_song_preview_matches_canonical_repertoire_constraints(override, message):
    with pytest.raises(MigrationError, match=message):
        preview(song_archive(song_record(**override)))


def test_song_preview_rejects_duplicate_attachment_identity():
    raw = b"%PDF duplicate"
    metadata = {"filename": "same.pdf", "label": "Same", "mime": "application/pdf", "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()}
    with pytest.raises(MigrationError, match="filenames must be unique"):
        preview(song_archive(song_record(attachments=[metadata, dict(metadata)]), {"same.pdf": raw}))


def test_song_commit_rolls_back_new_artifacts_when_canonical_identity_collides(tmp_path):
    raw = b"%PDF collision"
    metadata = {"filename": "score.pdf", "label": "Score", "mime": "application/pdf", "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()}
    source = song_archive(song_record(attachments=[metadata]), {"score.pdf": raw})
    checked = preview(source)
    home = tmp_path / "home"
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    existing = repertoire.create({"title": "Existing item"})
    with repertoire._db() as db:
        payload = dict(existing, id="song-1")
        payload.pop("attachment_availability", None)
        repertoire._write(db, payload)
    with pytest.raises(Exception, match="already exists"):
        commit(PeopleStore(home / "people"), {**source, "archive_digest": checked["archive_digest"],
               "review_token": checked["review_token"]}, repertoire=repertoire, artifacts=artifacts)
    slug = "legacy-song-" + hashlib.sha256(b"song-1:score.pdf").hexdigest()[:32]
    assert artifacts.get(slug) is None
    assert list((home / "capabilities" / "platform" / "migration-journals").glob("*.json")) == []


def test_song_failed_import_preserves_preexisting_identical_attachment(tmp_path):
    raw = b"%PDF shared"
    metadata = {"filename": "shared.pdf", "label": "Shared", "mime": "application/pdf", "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()}
    source = song_archive(song_record(attachments=[metadata]), {"shared.pdf": raw})
    checked = preview(source)
    home = tmp_path / "home"
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    existing = repertoire.create({"title": "Existing item"})
    with repertoire._db() as db:
        payload = dict(existing, id="song-1")
        payload.pop("attachment_availability", None)
        repertoire._write(db, payload)
    slug = "legacy-song-" + hashlib.sha256(b"song-1:shared.pdf").hexdigest()[:32]
    artifacts.create_binary(name="Shared", slug=slug, data=raw, mime="application/pdf", kind="pdf", source="manual")
    with pytest.raises(MigrationError, match="already exists"):
        commit(PeopleStore(home / "people"), {**source, "archive_digest": checked["archive_digest"],
               "review_token": checked["review_token"]}, repertoire=repertoire, artifacts=artifacts)
    assert artifacts.raw_bytes(slug, version=1) == (raw, "application/pdf")
    assert list((home / "capabilities" / "platform" / "migration-journals").glob("*.json")) == []


def test_song_restart_recovers_incomplete_import_and_owned_attachment(tmp_path):
    home = tmp_path / "home"
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    raw = b"%PDF interrupted"
    slug = "legacy-song-" + hashlib.sha256(b"song-recovery:score.pdf").hexdigest()[:32]
    artifacts.create_binary(name="Score", slug=slug, data=raw, mime="application/pdf", kind="pdf", source="import")
    token, digest, import_id = "a" * 64, "b" * 64, "platform-song-" + "b" * 64
    repertoire.import_song(import_id=import_id, source_fingerprint=token, item_id="song-recovery",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-02T00:00:00+00:00",
        data={"title": "Interrupted", "attachment_refs": [{"slug": slug, "version": 1}]},
        schedule={"stage": "new", "ease": 2.5, "interval": 0, "repetitions": 0, "due_at": None,
                  "last_practiced_at": None, "last_grade": None})
    journal_root = home / "capabilities" / "platform" / "migration-journals"
    journal_root.mkdir(parents=True)
    journal = {"schema": 1, "status": "artifacts_ready", "archive_digest": digest, "review_token": token,
               "import_id": import_id, "song_id": "song-recovery", "artifacts": [{"slug": slug, "version": 1,
               "kind": "pdf", "mime": "application/pdf", "sha256": hashlib.sha256(raw).hexdigest(), "owned": True,
               "ownership_fingerprint": artifacts.state_fingerprint(slug)}]}
    (journal_root / f"{digest}.json").write_text(json.dumps(journal))
    assert receipts(PeopleStore(home / "people"), repertoire=RepertoireStore(home / "capabilities" / "music", artifacts),
                    artifacts=NativeArtifactProvider(home / "artifacts")) == []
    with pytest.raises(Exception, match="not found"):
        repertoire.get("song-recovery")
    assert artifacts.get(slug) is None and not (journal_root / f"{digest}.json").exists()


def test_song_restart_refuses_recovery_after_user_edit(tmp_path):
    home = tmp_path / "home"
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    token, digest, import_id = "c" * 64, "d" * 64, "platform-song-" + "d" * 64
    repertoire.import_song(import_id=import_id, source_fingerprint=token, item_id="changed-song",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-02T00:00:00+00:00",
        data={"title": "Original"}, schedule={"stage": "new", "ease": 2.5, "interval": 0, "repetitions": 0,
        "due_at": None, "last_practiced_at": None, "last_grade": None})
    repertoire.update("changed-song", {"title": "User edit", "revision": 1})
    journal_root = home / "capabilities" / "platform" / "migration-journals"
    journal_root.mkdir(parents=True)
    journal = {"schema": 1, "status": "artifacts_ready", "archive_digest": digest, "review_token": token,
               "import_id": import_id, "song_id": "changed-song", "artifacts": []}
    (journal_root / f"{digest}.json").write_text(json.dumps(journal))
    with pytest.raises(MigrationError, match="changed and cannot be rolled back"):
        receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts)
    assert repertoire.get("changed-song")["title"] == "User edit"
    assert (journal_root / f"{digest}.json").exists()


def test_song_restart_fails_closed_on_malformed_recovery_journal(tmp_path):
    home = tmp_path / "home"
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    journal_root = home / "capabilities" / "platform" / "migration-journals"
    journal_root.mkdir(parents=True)
    path = journal_root / ("e" * 64 + ".json")
    path.write_text(json.dumps({"schema": 1, "status": "artifacts_ready", "archive_digest": "e" * 64}))
    with pytest.raises(MigrationError, match="unsupported shape"):
        receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts)
    assert path.exists()


def test_song_archives_reject_multiple_records_and_cross_store_mixes(tmp_path):
    two = domains_archive({"songs": [song_record("song-one"), song_record("song-two")]})
    checked = preview(two)
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    with pytest.raises(MigrationError, match="exactly one song"):
        commit(PeopleStore(tmp_path / "people"), {**two, "archive_digest": checked["archive_digest"],
               "review_token": checked["review_token"]}, repertoire=RepertoireStore(tmp_path / "music", artifacts),
               artifacts=artifacts)
    mixed = domains_archive({"songs": [song_record()], "ideas": [{"id": "39393939-3939-4939-8939-393939393939",
        "title": "Mixed", "status": "active", "oneLiner": "Mixed", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z"}]})
    with pytest.raises(MigrationError, match="separate atomic imports"):
        preview(mixed)


def test_supported_domains_restore_into_a_fresh_home_and_survive_reopen(tmp_path):
    home = tmp_path / "fresh-home"
    people = PeopleStore(home / "capabilities/communications")
    knowledge = KnowledgeStore(str(home / "knowledge.db"))
    projects, tasks = BoundHierarchy(home), BoundTasks(home)
    person_source = archive([{"id": "fresh-person", "name": "Fresh Person", "context": "Restored contact",
                              "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z"}])
    person_preview = preview(person_source)
    person_receipt, _ = commit(people, {**person_source, "archive_digest": person_preview["archive_digest"],
                                        "review_token": person_preview["review_token"]}, knowledge, projects, tasks)
    memory_id = "36363636-3636-4636-8636-363636363636"
    knowledge_source = domains_archive({"memories": [{"id": memory_id, "title": "Fresh memory", "content": "Restored content",
        "createdAt": "2026-02-01T00:00:00Z", "updatedAt": "2026-02-02T00:00:00Z"}]})
    knowledge_preview = preview(knowledge_source)
    knowledge_receipt, _ = commit(people, {**knowledge_source, "archive_digest": knowledge_preview["archive_digest"],
                                           "review_token": knowledge_preview["review_token"]}, knowledge, projects, tasks)
    project_id = "37373737-3737-4737-8737-373737373737"
    project_source = domains_archive({"projects": [{"id": project_id, "name": "Fresh project", "status": "active",
        "nextAction": "Continue", "createdAt": "2026-03-01T00:00:00Z", "updatedAt": "2026-03-02T00:00:00Z"}]})
    project_preview = preview(project_source)
    project_receipt, _ = commit(people, {**project_source, "archive_digest": project_preview["archive_digest"],
                                         "review_token": project_preview["review_token"]}, knowledge, projects, tasks)
    task_id = "38383838-3838-4838-8838-383838383838"
    task_source = domains_archive({"admin": [{"id": task_id, "title": "Fresh action", "status": "open", "nextAction": "Act",
        "createdAt": "2026-04-01T00:00:00Z", "updatedAt": "2026-04-02T00:00:00Z"}]})
    task_preview = preview(task_source)
    task_receipt, _ = commit(people, {**task_source, "archive_digest": task_preview["archive_digest"],
                                      "review_token": task_preview["review_token"]}, knowledge, projects, tasks)
    knowledge.close()

    reopened_people = PeopleStore(home / "capabilities/communications")
    reopened_knowledge = KnowledgeStore(str(home / "knowledge.db"))
    reopened_projects, reopened_tasks = BoundHierarchy(home), BoundTasks(home)
    assert reopened_people.people()[0]["name"] == "Fresh Person"
    assert reopened_knowledge.get_item(memory_id)["content"] == "Restored content"
    assert reopened_projects.get_project(project_id).name == "Fresh project"
    assert reopened_tasks._read_task(reopened_tasks._task_path(task_id)).title == "Fresh action"
    restored_receipts = receipts(reopened_people, reopened_knowledge, reopened_projects, reopened_tasks)
    assert {row["archive_digest"] for row in restored_receipts} == {
        person_receipt["archive_digest"], knowledge_receipt["archive_digest"],
        project_receipt["archive_digest"], task_receipt["archive_digest"],
    }
    reopened_knowledge.close()
