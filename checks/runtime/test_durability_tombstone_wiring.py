"""DURABILITY-AND-SYNC §4 / DAS-6c-iii-b — the hard-delete sites produce sync tombstones.

Wires record_tombstone into the flat-entity delete sites (delete_task, delete_task_list) so a
hard delete leaves a sync breadcrumb, and prune into the sync cycle so the log is bounded.
Projects (a subtree, many rows per delete) and comments (a sidecar rewrite, an update not an
entity unlink) are out of scope — see 6c-iii-c. The tombstone row id must equal the id the
exporter emits for that file (its path-stem under the entry dir).
"""

from __future__ import annotations

import asyncio
import json

from gideon.core.config.loader import config_dir
from gideon.operations.durability import shards
from gideon.operations.durability import tombstones as tomb


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDeleteTaskProducesTombstone:
    def test_delete_task_records_a_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        from gideon.engine.tasks.native import NativeTaskProvider, _tasks_dir

        assert config_dir() == tmp_path
        prov = NativeTaskProvider()
        task = _run(prov.create_task(title="doomed"))
        assert (_tasks_dir() / f"{task.id}.json").exists()

        assert _run(prov.delete_task(task.id)) is True
        assert not (_tasks_dir() / f"{task.id}.json").exists()
        markers = {t["id"] for t in tomb.read_tombstones(_tasks_dir())}
        assert task.id in markers

    def test_deleted_task_marker_rides_the_export(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        from gideon.engine.tasks.native import NativeTaskProvider

        prov = NativeTaskProvider()
        keep = _run(prov.create_task(title="keep"))
        drop = _run(prov.create_task(title="drop"))
        _run(prov.delete_task(drop.id))

        out = tmp_path / "shards"
        shards.export_shards(tmp_path, out)
        rows = [
            json.loads(ln)
            for ln in (out / "tasks" / "entities.jsonl").read_text().splitlines()
            if ln.strip()
        ]
        by_id = {r["id"]: r for r in rows}
        assert keep.id in by_id and not by_id[keep.id].get("deleted_at")
        assert by_id[drop.id].get("deleted_at")


class TestDeleteTaskListProducesTombstone:
    def test_delete_task_list_records_a_relpath_keyed_marker(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        from gideon.engine.tasks.hierarchy import HierarchyStore

        store = HierarchyStore()
        projects = store.list_projects()
        assert projects, "store should seed a default project"
        tl = store.create_task_list(project_id=projects[0].id, name="sprint-1")
        assert store.delete_task_list(tl.id) is True

        markers = {t["id"] for t in tomb.read_tombstones(tmp_path / "tasks")}
        assert f"task_lists/{tl.id}" in markers


class TestPruneWiredIntoCycle:
    def test_prune_helper_trims_old_markers(self, tmp_path, monkeypatch):
        from gideon.operations.durability import service

        tasks = tmp_path / "tasks"
        tasks.mkdir()
        tomb.record_tombstone(tasks, "ancient", now="2000-01-01T00:00:00+00:00")
        service._prune_tombstones(tmp_path, 900.0)
        assert tomb.read_tombstones(tasks) == []
