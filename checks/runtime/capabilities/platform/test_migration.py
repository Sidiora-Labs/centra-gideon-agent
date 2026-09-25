import base64
import hashlib
import io
import json
import tarfile
from copy import deepcopy
from contextlib import closing

import pytest

from gideon.workspace.capabilities.communications import PeopleStore
from gideon.cognition.knowledge.store import KnowledgeStore
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
    ({"brain/ideas/idea/index.json": b"{}"}, "brain"),
    ({"media/item.bin": b"media"}, "media"),
    ({"../portos-db.sql": b"select 1"}, "unsafe path"),
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
