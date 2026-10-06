import json
import os
from pathlib import Path

import pytest

from gideon.operations.durability import shards
from gideon.operations.durability.home_paths import LinkInTheWay


def test_unknown_export_destination_untouched(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    note = folder / "note.txt"
    note.write_text("keep")
    with pytest.raises(ValueError):
        shards.clear_shards(folder)
    assert note.read_text() == "keep"


def test_recognized_export_preserves_unrelated_files(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "shards": []})
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("keep")
    (tmp_path / "notes.txt").write_text("keep")
    folder = tmp_path / shards.inv.INVENTORY[0].id
    folder.mkdir()
    (folder / "part.jsonl").write_text("{}")
    shards.clear_shards(tmp_path)
    assert not folder.exists()
    assert not (tmp_path / "manifest.json").exists()
    assert (tmp_path / ".git/config").read_text() == "keep"
    assert (tmp_path / "notes.txt").read_text() == "keep"


def test_clear_export_link_preflight_leaves_original(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    manifest = export / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "shards": []}))
    outside = tmp_path / "outside"
    outside.write_text("keep")
    folder = export / shards.inv.INVENTORY[0].id
    folder.mkdir()
    os.link(outside, folder / "part.jsonl")
    with pytest.raises(LinkInTheWay):
        shards.clear_shards(export)
    assert manifest.exists()
    assert outside.read_text() == "keep"


def test_failed_fingerprints_preserve_last_success(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"failed": "old", "ok": "old"}))
    shards.mark_exported(
        state,
        shards.Changes(["failed", "ok"], {"failed": "new", "ok": "new"}),
        skipped={"failed/item": "refused"},
    )
    assert json.loads(state.read_text()) == {"failed": "old", "ok": "new"}


def test_deleted_entry_is_dirty_once(tmp_path):
    state = tmp_path / "state.json"
    entry = next(iter(shards.inv.export_entries()))
    state.write_text(json.dumps({entry.id: "old"}))
    changes = shards.dirty_entries(tmp_path, state)
    assert entry.id in changes
    shards.mark_exported(state, changes)
    assert entry.id not in shards.dirty_entries(tmp_path, state)


def test_fingerprint_rejects_linked_sqlite_before_checkpoint(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    path = tmp_path / "store.db"
    os.link(outside, path)
    with pytest.raises(LinkInTheWay):
        shards._fingerprint(path)
    assert outside.read_bytes() == b"untouched"
