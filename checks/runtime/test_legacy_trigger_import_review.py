from __future__ import annotations

import json

from gideon.automation.triggers.legacy_import import import_legacy
from gideon.automation.triggers.store import TriggerStore


def test_legacy_event_import_is_disabled_and_source_retires_once(tmp_path):
    legacy = tmp_path / "event_triggers.json"
    legacy.write_text(
        json.dumps(
            [
                {
                    "id": "mail-alert",
                    "name": "Mail alert",
                    "enabled": True,
                    "pattern": "MemoryUpdate",
                    "source": "memory",
                    "action_provider": "shell",
                    "action_config": {"command": "printf legacy"},
                }
            ]
        ),
        encoding="utf-8",
    )
    store = TriggerStore(base_dir=tmp_path / "config")

    first = import_legacy(tmp_path, store)
    assert first["pending"] == []
    assert first["retired"] == ["event_triggers"]
    imported = store.get("legacy-event-mail-alert").trigger
    assert not imported.enabled
    assert imported.state == "paused"
    assert imported.author == "legacy_import"
    assert imported.origin_harness == "legacy_import:event_triggers"
    assert imported.capabilities == {}
    assert (tmp_path / "event_triggers.json.migrated").is_file()

    second = import_legacy(tmp_path, store)
    assert second == {"imported": [], "retired": [], "pending": []}
    still_inert = store.get(imported.id).trigger
    assert not still_inert.enabled
    assert still_inert.state == "paused"
