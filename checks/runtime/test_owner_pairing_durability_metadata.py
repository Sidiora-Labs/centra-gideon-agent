from __future__ import annotations

import json
import shutil

from gideon.integrations.channel_trust import (
    allow_sender,
    cancel_owner_pairing,
    create_owner_pairing_code,
)
from gideon.operations.durability import inventory, shards, state_history
from gideon.workspace.snapshot import _derived_ignore


def test_owner_pairing_authority_is_not_exported_imported_or_rewound(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))
    monkeypatch.setenv("GIDEON_HOSTED", "0")
    allow_sender("channel-a", "saved-sender")
    code = create_owner_pairing_code("channel-a")
    entities = home / "entity_settings"
    authority = entities / "channel_owner_pairing.json"
    ordinary = entities / "channel_trust.json"
    assert authority.is_file() and ordinary.is_file()
    assert code not in authority.read_text()
    entry = next(
        item for item in inventory.all_entries() if item.id == "entity_settings"
    )
    assert "channel_owner_pairing.json" in entry.derived_within

    copy = tmp_path / "snapshot-entities"
    shutil.copytree(entities, copy, ignore=_derived_ignore("entity_settings", entities))
    assert (copy / "channel_trust.json").is_file()
    assert not (copy / "channel_owner_pairing.json").exists()

    exported = tmp_path / "export"
    result = shards.export_shards(home, exported, entries=["entity_settings"])
    imported = shards.import_shards(exported, entries=["entity_settings"])
    assert {row["id"] for row in imported.rows["entity_settings"]} == {"channel_trust"}

    legacy_rows = [
        *imported.rows["entity_settings"],
        {"id": "channel_owner_pairing", "data": json.loads(authority.read_text())},
    ]
    result.shards = shards._write_shard(
        exported, "entity_settings/entities.jsonl", legacy_rows
    )
    shards._write_manifest(home, exported, result)
    assert shards.validate(exported).ok
    legacy = shards.import_shards(exported, entries=["entity_settings"])
    assert {row["id"] for row in legacy.rows["entity_settings"]} == {"channel_trust"}

    (home / "config.json").write_text('{"value":1}\n')
    root = next(
        item
        for item in state_history.roots(home=home, workspace=home / "workspace")
        if item.id == "config"
    )
    ordinary_before = ordinary.read_bytes()
    first = state_history.commit(root, home=home)
    assert first
    assert cancel_owner_pairing("channel-a")
    cancelled = authority.read_bytes()
    allow_sender("channel-a", "later-sender")
    state_history.commit(root, home=home)
    state_history.rollback(root, first, home=home)
    assert authority.read_bytes() == cancelled
    assert ordinary.read_bytes() == ordinary_before
    objects = state_history._git(
        root, "rev-list", "--all", "--objects", home=home
    ).stdout
    assert "channel_owner_pairing.json" not in objects
