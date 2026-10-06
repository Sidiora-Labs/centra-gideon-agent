"""Deletion agreement and review through actual local filesystem sync copies."""

import json

from test_durability_convergence_e2e import FolderTransport
from test_sync_latest_copy_filesystem import cycle, task

from gideon.operations.durability.ancestors import Ancestors, Deletion
from gideon.operations.durability.conflict_merge import pending
from gideon.operations.durability.conflict_resolve import resolve_conflict
from gideon.operations.durability.conflicts import ConflictQueue


def homes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    task(a, "original")
    transport = FolderTransport(tmp_path / "store")
    assert cycle(transport, a, "a").ok
    assert cycle(transport, b, "b").ok
    assert cycle(transport, a, "a").ok
    return a, b, transport


def test_known_delete_propagates_and_owner_can_restore(tmp_path):
    a, b, transport = homes(tmp_path)
    (a / "tasks" / "item.json").unlink()
    assert cycle(transport, a, "a").ok
    assert cycle(transport, b, "b").ok
    assert not (b / "tasks" / "item.json").exists()
    mark = Ancestors(b / "sync").deleted("tasks")["item"]
    assert mark.by == "a"
    task(a, "restored")
    assert cycle(transport, a, "a").ok
    assert cycle(transport, b, "b").ok
    assert json.loads((b / "tasks" / "item.json").read_text())["title"] == "restored"


def test_delete_vs_unseen_edit_requires_choice_and_never_model_draft(tmp_path):
    a, b, transport = homes(tmp_path)
    (a / "tasks" / "item.json").unlink()
    task(b, "new unseen edit")
    assert cycle(transport, a, "a").ok
    assert cycle(transport, b, "b").ok
    assert (
        json.loads((b / "tasks" / "item.json").read_text())["title"]
        == "new unseen edit"
    )
    queue = ConflictQueue(b)
    records = queue.items()
    assert len(records) == 1 and records[0].deleted == "there"
    assert pending(queue) == []
    applied = resolve_conflict(b, records[0].id, "take_remote")
    assert applied.ok and applied.removed == 1
    assert not (b / "tasks" / "item.json").exists()


def test_local_deletion_declines_known_old_peer_copy(tmp_path):
    a, b, transport = homes(tmp_path)
    (b / "tasks" / "item.json").unlink()
    # A changed unrelated record forces a new peer copy containing the old item.
    (a / "tasks" / "other.json").write_text(json.dumps({"id": "other", "title": "new"}))
    assert cycle(transport, a, "a").ok
    assert cycle(transport, b, "b").ok
    assert not (b / "tasks" / "item.json").exists()
    assert (b / "tasks" / "other.json").exists()


def test_unreadable_record_does_not_become_a_deletion(tmp_path):
    a, b, transport = homes(tmp_path)
    (a / "tasks" / "item.json").write_text("{bad")
    result = cycle(transport, a, "a")
    assert not result.ok
    assert "item" not in Ancestors(a / "sync").deleted("tasks")
    assert (b / "tasks" / "item.json").exists()


def test_delete_horizon_only_expires_after_explicit_publish_cleanup(tmp_path):
    ledger = Ancestors(tmp_path)
    ledger.publish("tasks", {"x": "sha"}, now="2026-01-01T00:00:00+00:00")
    ledger.publish("tasks", {}, now="2026-01-02T00:00:00+00:00")
    ledger.save()
    assert ledger.deletions()["tasks"]["x"].held == ("sha",)
    ledger.forget_old_deletes("2026-02-01T00:00:00+00:00")
    assert ledger.deletions()
    ledger.forget_old_deletes("2026-05-01T00:00:00+00:00")
    assert ledger.deletions() == {}


def test_old_delete_still_rides_copy_when_whole_store_absent(tmp_path):
    import shutil

    from gideon.operations.durability.shards import export_shards, import_shards

    home = tmp_path / "home"
    home.mkdir()
    out = tmp_path / "export"
    result = export_shards(
        home,
        out,
        deletions={"tasks": {"x": Deletion("2026-10-06T12:00:00+00:00", ("sha",))}},
    )
    assert not result.skipped
    assert import_shards(out).rows["tasks"] == [
        {"id": "x", "deleted_at": "2026-10-06T12:00:00+00:00", "held": ["sha"]}
    ]


def test_collection_delete_preserves_envelope_and_local_controls(tmp_path):
    from gideon.operations.durability import inventory as inv
    from gideon.operations.durability.conflicts import compared, row_sha
    from gideon.operations.durability.reconcile import reconcile_entry

    home = tmp_path / "home"
    home.mkdir()
    entry = inv.by_id("crons")
    document = [
        {"id": "x", "name": "one", "enabled": True},
        {"id": "y", "name": "two", "enabled": True},
    ]
    (home / entry.path).write_text(json.dumps(document))
    row = {"id": "x", "data": document[0]}
    result = reconcile_entry(
        home,
        entry,
        [
            {
                "id": "x",
                "deleted_at": "2026-10-06",
                "held": [row_sha(compared(entry, row))],
            }
        ],
        peer_id="peer",
    )
    assert result.verdict == "consumed"
    remaining = json.loads((home / entry.path).read_text())
    assert [row["id"] for row in remaining] == ["y"]
    assert remaining[0]["enabled"] is True
