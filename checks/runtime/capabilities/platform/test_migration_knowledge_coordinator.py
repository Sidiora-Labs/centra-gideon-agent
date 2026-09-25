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
from gideon.workspace.capabilities.platform.migration import FORMAT, MigrationError, commit, preview, receipts


BUCKET = "71717171-7171-4171-8171-717171717171"
IDEA = "72727272-7272-4272-8272-727272727272"
MEMORY = "73737373-7373-4373-8373-737373737373"
LINK = "74747474-7474-4474-8474-747474747474"
ADMIN = "75757575-7575-4575-8575-757575757575"
THREAD = "knowledge-coordinated-thread"


def records_archive(collections):
    files = {}
    for domain, rows in collections.items():
        files[f"brain/{domain}/index.json"] = json.dumps({
            "schemaVersion": 1, "type": domain, "updatedAt": "2026-09-25T08:00:00Z", "config": {}}).encode()
        files.update({f"brain/{domain}/{row['id']}/index.json": json.dumps(row).encode() for row in rows})
    manifest = {"generatedAt": "2026-09-25T08:00:00Z", "fileCount": len(files),
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        payloads = {"snapshot-knowledge/manifest.json": json.dumps(manifest).encode(),
                    **{f"snapshot-knowledge/data/{name}": value for name, value in files.items()}}
        for name, value in payloads.items():
            info = tarfile.TarInfo(name); info.size = len(value); archive.addfile(info, io.BytesIO(value))
    return {"format": FORMAT, "content": base64.b64encode(output.getvalue()).decode()}


def mixed_source(*, broken=False, inbox=False):
    now = "2026-09-25T08:00:00Z"
    domains = {
        "buckets": [{"id": BUCKET, "name": "Research", "color": "accent", "icon": "book", "order": 2,
                     "createdAt": now, "updatedAt": now}],
        "ideas": [{"id": IDEA, "title": "Coordinate imports", "status": "active", "oneLiner": "One recovery plan",
                   "notes": "Across canonical stores", "tags": ["migration"], "createdAt": now, "updatedAt": now}],
        "journals": [{"id": "2026-09-25", "date": "2026-09-25", "content": "Coordinator verified",
                      "segments": [{"text": "Coordinator verified", "at": now, "source": "text"}],
                      "createdAt": now, "updatedAt": now}],
        "memories": [{"id": MEMORY, "title": "Recovery rule", "content": "Freeze all stores on drift", "mood": "focused",
                      "tags": ["durability"], "createdAt": now, "updatedAt": now}],
        "links": [{"id": LINK, "title": "Runbook", "url": "https://example.test/runbook", "description": "Recovery",
                   "note": "Canonical link", "linkType": "reference", "tags": ["migration"], "bucketId": BUCKET,
                   "bucketOrder": 1, "createdAt": now, "updatedAt": now}],
        "admin": [{"id": ADMIN, "title": "Publish knowledge", "status": "open", "nextAction": "Verify references",
                   "notes": "Coordinator task", "createdAt": now, "updatedAt": now}],
        "threads": [{"id": THREAD, "title": "Review knowledge migration", "status": "open", "priority": "high",
                     "nextAction": "Inspect journal", "notes": "Final task", "tags": [], "pinned": False,
                     "refs": ([{"kind": "brain.project", "id": "missing-project", "label": "Missing"}] if broken else [
                         {"kind": "brain.idea", "id": IDEA, "label": "Idea"},
                         {"kind": "brain.memory", "id": MEMORY, "label": "Memory"},
                         {"kind": "brain.link", "id": LINK, "label": "Link"},
                         {"kind": "brain.journal", "id": "2026-09-25", "label": "Journal"},
                         {"kind": "brain.admin", "id": ADMIN, "label": "Admin"}]),
                     "createdAt": now, "updatedAt": now}],
    }
    if inbox:
        domains["inbox"] = [{"id": "76767676-7676-4676-8676-767676767676", "capturedText": "Keep immutable",
                              "capturedAt": now, "source": "brain_ui", "status": "needs_review"}]
    return records_archive(domains)


def opened(tmp_path):
    home = tmp_path / "home"
    return (home, PeopleStore(home / "capabilities" / "communications"),
            KnowledgeStore(str(home / "knowledge.db")), BoundHierarchy(home), BoundTasks(home))


def reviewed(source):
    checked = preview(source)
    return {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}


def test_five_knowledge_families_share_schema_two_journal_refs_restart_and_replay(tmp_path):
    source = mixed_source(); home, people, knowledge, projects, tasks = opened(tmp_path)
    receipt, created = commit(people, reviewed(source), knowledge, projects, tasks)
    assert created is True
    assert receipt["domains"] == {"admin": 1, "buckets": 1, "ideas": 1, "journals": 1,
                                  "links": 1, "memories": 1, "threads": 1}
    journal_path = migration._canonical_coordinator_root(tasks) / f'{receipt["archive_digest"]}.json'
    before = journal_path.read_bytes(); journal = json.loads(before)
    assert [step["adapter"] for step in journal["steps"]] == [
        "collection", "knowledge", "knowledge", "knowledge", "knowledge", "task", "task"]
    assert knowledge.get_item(IDEA)["guid"] == "ideas:" + IDEA
    assert knowledge.db.execute(
        "SELECT added_at FROM collection_items WHERE collection_id=? AND item_id=?", (BUCKET, LINK)).fetchone()[0] == "2026-09-25T08:00:00Z"
    task = tasks._read_task(tasks._task_path(THREAD))
    assert [ref["canonical_id"] for ref in task.evidence[0]["references"]] == [IDEA, MEMORY, LINK, "2026-09-25", ADMIN]
    knowledge.close()
    restarted_knowledge = KnowledgeStore(str(home / "knowledge.db"))
    try:
        replay, replay_created = commit(PeopleStore(home / "capabilities" / "communications"), reviewed(source),
                                        restarted_knowledge, BoundHierarchy(home), BoundTasks(home))
        assert replay_created is False and replay == receipt and journal_path.read_bytes() == before
        assert receipts(people, restarted_knowledge, projects, tasks) == [receipt]
    finally:
        restarted_knowledge.close()


def test_late_task_failure_reverse_compensates_knowledge_items_membership_and_bucket(tmp_path):
    _, people, knowledge, projects, tasks = opened(tmp_path)
    with pytest.raises(MigrationError, match="missing canonical project"):
        commit(people, reviewed(mixed_source(broken=True)), knowledge, projects, tasks)
    assert knowledge.db.execute("SELECT count(*) FROM items WHERE id IN (?,?,?,?)",
                                (IDEA, MEMORY, LINK, "2026-09-25")).fetchone()[0] == 0
    assert knowledge.db.execute("SELECT count(*) FROM collections WHERE id=?", (BUCKET,)).fetchone()[0] == 0
    assert knowledge.db.execute("SELECT count(*) FROM collection_items WHERE collection_id=?", (BUCKET,)).fetchone()[0] == 0
    assert not tasks._task_path(ADMIN).exists() and list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()


def test_knowledge_edit_is_drift_and_freezes_incomplete_plan(tmp_path):
    _, people, knowledge, projects, tasks = opened(tmp_path)
    receipt, _ = commit(people, reviewed(mixed_source()), knowledge, projects, tasks)
    knowledge.db.execute("UPDATE items SET content='user edit',updated_at='2026-09-25T09:00:00Z' WHERE id=?", (MEMORY,))
    knowledge.db.commit()
    path = migration._canonical_coordinator_root(tasks) / f'{receipt["archive_digest"]}.json'
    journal = json.loads(path.read_text()); journal["status"] = "applying"; journal["steps"][-1]["state"] = "pending"
    tasks._task_path(THREAD).unlink(); migration._write_journal(path, journal)
    with pytest.raises(MigrationError, match="identity drift|compensation refused"):
        receipts(people, knowledge, projects, tasks)
    assert knowledge.get_item(MEMORY)["content"] == "user edit" and knowledge.get_item(IDEA) is not None
    assert tasks._task_path(ADMIN).exists() and path.exists()
    knowledge.close()


def test_mixed_inbox_is_rejected_before_any_canonical_mutation(tmp_path):
    _, _, knowledge, _, tasks = opened(tmp_path)
    with pytest.raises(MigrationError, match="capture history is immutable"):
        preview(mixed_source(inbox=True))
    assert knowledge.db.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    assert knowledge.db.execute("SELECT count(*) FROM collections").fetchone()[0] == 0
    assert list(migration._canonical_coordinator_root(tasks).glob("*.json")) == []
    knowledge.close()
