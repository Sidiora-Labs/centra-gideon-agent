from __future__ import annotations

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.review import TriggerReviewStore
from gideon.automation.triggers.store import TriggerStore
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import state_history as history


def test_trigger_review_authority_is_not_exported_or_rewound(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    triggers = TriggerStore(base_dir=tmp_path)
    triggers.upsert(
        Trigger(
            id="clock:review-metadata",
            name="Review metadata",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 900},
            workflow={"inline": {"provider": "run-workflow", "config": {"workflow": "sample"}}},
        )
    )
    reviews = TriggerReviewStore(tmp_path)
    pending = reviews.add_boot_observations(
        triggers,
        {"review": {"rows": [{"trigger_id": "clock:review-metadata", "scheduled_for": 1200}], "summaries": []}},
        [],
        now=1500,
    )
    assert len(pending) == 1

    entry = next(row for row in inv.all_entries() if row.id == "trigger_review_queue")
    assert (entry.path, entry.domain, entry.merge) == (
        "trigger-review.json",
        inv.DOMAIN_AUTOMATION,
        inv.MERGE_REPLACE_ONLY,
    )
    assert entry.kind == inv.KIND_JSON_FILE and entry.secret and entry.derived
    assert entry.id not in {row.id for row in inv.backup_entries()}
    assert entry.id not in {row.id for row in inv.export_entries()}

    review_path = tmp_path / entry.path
    review_bytes = review_path.read_bytes()
    config = tmp_path / "config.json"
    config.write_text('{"value":1}\n', encoding="utf-8")
    root = next(
        row
        for row in history.roots(home=tmp_path, workspace=tmp_path / "workspace")
        if row.id == "config"
    )
    first = history.commit(root, home=tmp_path)
    config.write_text('{"value":2}\n', encoding="utf-8")
    history.commit(root, home=tmp_path)
    assert first
    history.rollback(root, first, home=tmp_path)

    exclude = (history.git_dir(root, home=tmp_path) / "info" / "exclude").read_text()
    assert "\ntrigger-review.json\n" in exclude
    objects = history._git(root, "rev-list", "--all", "--objects", home=tmp_path).stdout
    assert "trigger-review.json" not in objects
    assert config.read_text(encoding="utf-8") == '{"value":1}\n'
    assert review_path.read_bytes() == review_bytes
