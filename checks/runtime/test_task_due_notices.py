import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def active_home(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    from gideon.core.config.loader import AppConfig

    config = AppConfig.load()
    config.dashboard.username = "due-owner"
    config.save()
    return home


def _notification_settings(home: Path, *, quiet: bool) -> None:
    path = home / "entity_settings" / "notifications.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "quiet_hours_enabled": quiet,
                "quiet_hours_start": "00:00",
                "quiet_hours_end": "23:59",
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_due_notice_waits_for_quiet_hours_and_survives_restart(
    active_home, tmp_path
):
    import argparse
    import tarfile

    from gideon.engine.tasks import registry
    from gideon.engine.tasks.due_notices import LEDGER_NAME, sweep
    from gideon.engine.tasks.native import NativeTaskProvider
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.operations.durability import shards
    from gideon.operations.durability.inventory import INVENTORY
    from gideon.workspace.snapshot import snapshot_main

    registry.register_provider(NativeTaskProvider())
    now = (
        datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    )
    due = (now.date() + timedelta(days=1)).isoformat()
    task = await registry.create_task(
        provider_name="native",
        title="Ship the release notes",
        due=due,
        due_reminder=True,
    )
    opted_out = await registry.create_task(
        provider_name="native", title="No reminder", due=due, due_reminder=False
    )
    foreign = await registry.create_task(
        provider_name="native", title="Other owner", due=due, assignee="another-owner"
    )
    finished = await registry.create_task(
        provider_name="native", title="Already finished", due=due
    )
    await registry.update_task(finished.id, provider_name="native", status="done")
    _notification_settings(active_home, quiet=True)
    state = ConsoleState(None, start_time=0)

    assert await sweep(state, now=now) == 0
    ledger = active_home / "tasks" / LEDGER_NAME
    assert not ledger.exists()

    _notification_settings(active_home, quiet=False)
    assert await sweep(state, now=now) == 1
    assert await sweep(ConsoleState(None, start_time=0), now=now) == 0
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    assert payload == {"notified": {task.id: due}}
    notes = [
        json.loads(line)
        for line in (active_home / "notifications.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    note = next(row for row in notes if row.get("task_id") == task.id)
    assert note["statusUrl"] == f"#/tasks?open={task.id}"
    assert all(
        row.get("task_id") not in {opted_out.id, foreign.id, finished.id}
        for row in notes
    )

    await registry.update_task(
        task.id, provider_name="native", due=now.date().isoformat()
    )
    assert await sweep(state, now=now) == 1
    assert (
        json.loads(ledger.read_text(encoding="utf-8"))["notified"][task.id]
        == now.date().isoformat()
    )

    out = tmp_path / "shards"
    shards.export_shards(active_home, out, entries=["tasks"])
    imported = shards.import_shards(out, entries=["tasks"])
    rows = imported.rows["tasks"]
    assert any(row["id"] == task.id for row in rows)
    assert all(row["id"] != LEDGER_NAME.removesuffix(".json") for row in rows)

    snapshots = tmp_path / "snapshots"
    assert (
        snapshot_main(
            parsed=argparse.Namespace(
                output_dir=str(snapshots), keep=10, list_snapshots=False
            )
        )
        == 0
    )
    archive = next(snapshots.glob("gideon-snapshot-*.tar.gz"))
    with tarfile.open(archive, "r:gz") as snapshot:
        assert not any(
            name.endswith(f"tasks/{LEDGER_NAME}") for name in snapshot.getnames()
        )
    tasks_entry = next(entry for entry in INVENTORY if entry.id == "tasks")
    assert tasks_entry.derived_within == (LEDGER_NAME,)
