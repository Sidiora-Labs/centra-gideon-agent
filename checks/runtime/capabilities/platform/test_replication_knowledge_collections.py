import copy
import sqlite3

import pytest

from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.platform import replication_adapters
from gideon.workspace.capabilities.platform import replication_knowledge_collections as adapter


NOW = "2026-09-25T14:00:00+00:00"


def knowledge(home):
    return KnowledgeStore(str(knowledge_db_path(home)))


def create_item(home, title):
    store = knowledge(home)
    try:
        identity = store.create_typed_item(
            item_type="bookmark", title=title, content="Source-grounded note for " + title,
            url="https://example.test/" + title.casefold().replace(" ", "-"),
            provider="manual", tags=["replication"],
        )
        assert identity
        return identity
    finally:
        store.db.close()


def replicate_items(source, target):
    rows = replication_adapters.read_rows(source, "knowledge.items")
    result = replication_adapters.apply_rows(
        target, "knowledge.items", rows, {}, conflicts.ConflictQueue(target), NOW,
    )
    return rows, result.new_ancestors


def create_collections(home, item_ids):
    store = knowledge(home)
    try:
        manual = store.create_collection(name="Research shelf", icon="link-bucket")
        smart = store.create_collection(name="Unread notes", kind="smart", query="status:unread", icon="search")
        assert store.add_to_collection(manual, item_ids[0]) is True
        assert store.add_to_collection(manual, item_ids[1]) is True
        return manual, smart
    finally:
        store.db.close()


def entries(home):
    return [
        {"entry_id": entry_id, "rows": adapter.read_rows(home, entry_id)}
        for entry_id in adapter.ENTRIES
    ]


def empty_ancestors():
    return {entry_id: {} for entry_id in adapter.ENTRIES}


def result_ancestors(results):
    return {entry_id: results[entry_id].new_ancestors for entry_id in adapter.ENTRIES}


def collection(home, identity):
    store = knowledge(home)
    try:
        return store.get_collection(identity)
    finally:
        store.db.close()


def test_two_home_current_collections_and_memberships_preserve_exact_canonical_rows(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Alpha"), create_item(source, "Beta")]
    item_rows, _ = replicate_items(source, target)
    manual, smart = create_collections(source, item_ids)

    projected = entries(source)
    collections, memberships = projected
    assert collections["entry_id"] == adapter.COLLECTION_ENTRY
    assert memberships["entry_id"] == adapter.MEMBERSHIP_ENTRY
    assert {row["id"] for row in collections["rows"]} == {manual, smart}
    assert len(memberships["rows"]) == 2
    assert all(set(row["data"]) == {"collection_id", "item_id", "added_at"} for row in memberships["rows"])
    assert all("item_count" not in row["data"] for row in collections["rows"])
    adapter.validate_entries(projected, source)

    results = adapter.apply_entries(
        target, projected, empty_ancestors(), conflicts.ConflictQueue(target), NOW,
    )
    assert (results[adapter.COLLECTION_ENTRY].added, results[adapter.MEMBERSHIP_ENTRY].added) == (2, 2)
    assert entries(target) == projected
    target_store = knowledge(target)
    try:
        assert [row["id"] for row in target_store.resolve_collection(manual)] == list(reversed(item_ids))
        assert target_store.get_collection(smart)["query"] == "status:unread"
        assert target_store.db.execute("SELECT count(*) FROM items_fts").fetchone()[0] == len(item_rows)
    finally:
        target_store.db.close()


def test_fast_forward_exact_replay_and_membership_tombstone(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Gamma"), create_item(source, "Delta")]
    replicate_items(source, target)
    manual, _ = create_collections(source, item_ids)
    baseline = entries(source)
    initial = adapter.apply_entries(
        target, baseline, empty_ancestors(), conflicts.ConflictQueue(target), NOW,
    )
    ancestors = result_ancestors(initial)

    source_store = knowledge(source)
    try:
        assert source_store.update_collection(manual, name="Field research", icon="archive")
        assert source_store.remove_from_collection(manual, item_ids[0])
    finally:
        source_store.db.close()
    changed = entries(source)
    forwarded = adapter.apply_entries(
        target, changed, ancestors, conflicts.ConflictQueue(target),
        "2026-09-25T14:05:00+00:00",
    )
    assert forwarded[adapter.COLLECTION_ENTRY].updated == 1
    assert forwarded[adapter.MEMBERSHIP_ENTRY].removed == 1
    assert collection(target, manual)["name"] == "Field research"
    target_store = knowledge(target)
    try:
        assert [row["id"] for row in target_store.resolve_collection(manual)] == [item_ids[1]]
    finally:
        target_store.db.close()

    replayed = adapter.apply_entries(
        target, changed, result_ancestors(forwarded), conflicts.ConflictQueue(target),
        "2026-09-25T14:10:00+00:00",
    )
    for result in replayed.values():
        assert (result.added, result.updated, result.removed, result.conflicts) == (0, 0, 0, 0)


def test_collection_conflict_selected_restore_keeps_local_fields(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Epsilon"), create_item(source, "Zeta")]
    replicate_items(source, target)
    manual, _ = create_collections(source, item_ids)
    initial = adapter.apply_entries(
        target, entries(source), empty_ancestors(), conflicts.ConflictQueue(target), NOW,
    )
    ancestors = result_ancestors(initial)
    source_store, target_store = knowledge(source), knowledge(target)
    try:
        assert source_store.update_collection(manual, name="Remote title", icon="remote")
        assert target_store.update_collection(manual, name="Local title", icon="local")
    finally:
        source_store.db.close()
        target_store.db.close()

    queue = conflicts.ConflictQueue(target)
    held = adapter.apply_entries(
        target, entries(source), ancestors, queue, "2026-09-25T14:15:00+00:00",
    )
    assert held[adapter.COLLECTION_ENTRY].conflicts == 1
    assert collection(target, manual)["name"] == "Local title"
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1 and pending[0].entry_id == adapter.COLLECTION_ENTRY
    restored = adapter.restore_fields(
        target, pending[0].id, ["name"], "2000-01-01T00:00:00+00:00",
    )
    merged = collection(target, manual)
    assert restored["fields"] == ["name"]
    assert merged["name"] == "Remote title"
    assert merged["icon"] == "local"
    assert adapter._timestamp(merged["updated_at"], "updated_at") > adapter._timestamp(merged["created_at"], "created_at")
    assert conflicts.ConflictQueue(target).get(pending[0].id).status == conflicts.STATUS_RESOLVED


def test_collection_tombstone_removes_memberships_but_preserves_items(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Eta"), create_item(source, "Theta")]
    replicate_items(source, target)
    manual, smart = create_collections(source, item_ids)
    initial = adapter.apply_entries(
        target, entries(source), empty_ancestors(), conflicts.ConflictQueue(target), NOW,
    )
    source_store = knowledge(source)
    try:
        assert source_store.delete_collection(manual)
        assert source_store.delete_collection(smart)
    finally:
        source_store.db.close()
    removed = adapter.apply_entries(
        target, entries(source), result_ancestors(initial), conflicts.ConflictQueue(target),
        "2026-09-25T14:30:00+00:00",
    )
    assert removed[adapter.COLLECTION_ENTRY].removed == 2
    assert removed[adapter.MEMBERSHIP_ENTRY].removed == 2
    target_store = knowledge(target)
    try:
        assert target_store.list_collections() == []
        assert all(target_store.get_item(item_id) is not None for item_id in item_ids)
        assert target_store.db.execute("SELECT count(*) FROM collection_items").fetchone()[0] == 0
    finally:
        target_store.db.close()


def test_missing_item_and_missing_collection_fail_before_any_mutation(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Iota"), create_item(source, "Kappa")]
    manual, _ = create_collections(source, item_ids)
    canonical = entries(source)
    with pytest.raises(ValueError, match="missing canonical item"):
        adapter.apply_entries(
            target, canonical, empty_ancestors(), conflicts.ConflictQueue(target), NOW,
        )
    assert adapter.read_rows(target, adapter.COLLECTION_ENTRY) == []
    assert adapter.read_rows(target, adapter.MEMBERSHIP_ENTRY) == []

    replicate_items(source, target)
    orphaned = copy.deepcopy(canonical)
    orphaned[0]["rows"] = [row for row in orphaned[0]["rows"] if row["id"] != manual]
    with pytest.raises(ValueError, match="missing collection"):
        adapter.apply_entries(
            target, orphaned, empty_ancestors(), conflicts.ConflictQueue(target), NOW,
        )
    assert adapter.read_rows(target, adapter.COLLECTION_ENTRY) == []
    assert adapter.read_rows(target, adapter.MEMBERSHIP_ENTRY) == []


def test_exact_schema_hash_identity_duplicate_names_and_authority_fields_are_rejected(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Lambda"), create_item(source, "Mu")]
    replicate_items(source, target)
    create_collections(source, item_ids)
    canonical = entries(source)
    invalid = []

    value = copy.deepcopy(canonical)
    value[0]["rows"][0]["data"]["request_id"] = "local-ledger"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[0]["rows"][0]["data"]["credential_ref"] = "secret"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[0]["rows"][0]["data"]["kind"] = "execution"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[0]["rows"][0]["data"]["updated_at"] = "yesterday"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[1]["rows"][0]["id"] = "0" * 64
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[1]["rows"].append(copy.deepcopy(value[1]["rows"][0]))
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[0]["rows"][1]["data"]["name"] = value[0]["rows"][0]["data"]["name"].swapcase()
    invalid.append(value)

    for payload in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries(payload, source)
        with pytest.raises(ValueError):
            adapter.apply_entries(
                target, payload, empty_ancestors(), conflicts.ConflictQueue(target), NOW,
            )
        assert adapter.read_rows(target, adapter.COLLECTION_ENTRY) == []
        assert adapter.read_rows(target, adapter.MEMBERSHIP_ENTRY) == []
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([])
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries(list(reversed(canonical)))


def test_membership_conflict_is_held_without_overwriting_added_at(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    item_ids = [create_item(source, "Nu"), create_item(source, "Xi")]
    replicate_items(source, target)
    manual, _ = create_collections(source, item_ids)
    initial = adapter.apply_entries(
        target, entries(source), empty_ancestors(), conflicts.ConflictQueue(target), NOW,
    )
    ancestors = result_ancestors(initial)
    source_membership = adapter.read_rows(source, adapter.MEMBERSHIP_ENTRY)[0]
    target_membership = adapter.read_rows(target, adapter.MEMBERSHIP_ENTRY)[0]
    source_store, target_store = knowledge(source), knowledge(target)
    try:
        source_store.db.execute(
            "UPDATE collection_items SET added_at=? WHERE collection_id=? AND item_id=?",
            ("2026-09-25T15:00:00+00:00", source_membership["data"]["collection_id"], source_membership["data"]["item_id"]),
        )
        source_store.db.commit()
        target_store.db.execute(
            "UPDATE collection_items SET added_at=? WHERE collection_id=? AND item_id=?",
            ("2026-09-25T15:05:00+00:00", target_membership["data"]["collection_id"], target_membership["data"]["item_id"]),
        )
        target_store.db.commit()
    finally:
        source_store.db.close()
        target_store.db.close()
    queue = conflicts.ConflictQueue(target)
    held = adapter.apply_entries(target, entries(source), ancestors, queue, "2026-09-25T15:10:00+00:00")
    assert held[adapter.MEMBERSHIP_ENTRY].conflicts == 1
    actual = {row["id"]: row for row in adapter.read_rows(target, adapter.MEMBERSHIP_ENTRY)}
    assert actual[target_membership["id"]]["data"]["added_at"] == "2026-09-25T15:05:00+00:00"
    pending = [row for row in queue.items(status=conflicts.STATUS_NEEDS_REVIEW) if row.entry_id == adapter.MEMBERSHIP_ENTRY]
    assert len(pending) == 1 and pending[0].entity_id == target_membership["id"]
