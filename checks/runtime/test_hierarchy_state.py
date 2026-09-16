import json

import pytest

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.models import Project
from gideon.operations.durability.tombstones import read_tombstones


def test_real_cascade_carries_only_synced_rows_and_preserves_other_projects(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = HierarchyStore()
    project = store.create_project("Disposable")
    other = store.create_project("Keep")
    task_list = store.create_task_list("Work", project_id=project.id)
    sibling = store.create_task_list("Work", project_id=other.id)
    (store.context_dir(project.id) / "facts.json").write_text("{}")
    (store.worktrees_dir(project.id) / "generated.json").write_text("{}")
    assert store.delete_project(project.id)
    assert not store._project_dir(project.id).exists()
    assert store.get_task_list(task_list.id) is None
    assert store.get_task_list(sibling.id).project_id == other.id
    project_ids = {row["id"] for row in read_tombstones(tmp_path / "projects")}
    assert project_ids == {f"{project.id}/project", f"{project.id}/context/facts"}
    assert {row["id"] for row in read_tombstones(tmp_path / "tasks")} == {
        f"task_lists/{task_list.id}"
    }


def test_migration_keeps_current_record_and_skips_invalid_legacy_identifier(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = HierarchyStore()
    existing = store.create_project("Current")
    legacy = tmp_path / "tasks" / "projects"
    legacy.mkdir(parents=True)
    (legacy / "same.json").write_text(
        json.dumps(Project(id=existing.id, name="Older").to_dict())
    )
    (legacy / "unsafe.json").write_text(
        json.dumps(Project(id="../escape", name="Invalid").to_dict())
    )
    (legacy / "chore.json").write_text(
        json.dumps(Project(id="chore", name="Chore").to_dict())
    )
    (tmp_path / "projects" / "obsolete.json").write_text("{}")
    store.ensure_defaults()
    assert store.get_project(existing.id).name == "Current"
    assert store.get_project("chore").name == "Personal"
    assert not legacy.exists() and not (tmp_path / "escape").exists()
    assert not (tmp_path / "projects" / "obsolete.json").exists()
    identifiers = {row.id for row in store.list_projects()}
    store.ensure_defaults()
    assert identifiers == {row.id for row in store.list_projects()}


def test_rejected_patch_does_not_persist_earlier_fields_and_list_update_keeps_contract(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = HierarchyStore()
    project = store.create_project("Original")
    before = store._project_path(project.id).read_bytes()
    with pytest.raises(ValueError, match="status must"):
        store.update_project(
            project.id, name="Changed", brief="Changed", status="unknown"
        )
    assert store._project_path(project.id).read_bytes() == before
    first = store.create_task_list("First", project_id=project.id)
    second = store.create_task_list("Second", project_id=project.id)
    store.update_task_list(second.id, name="First", project_id="")
    assert [row.id for row in store.list_task_lists(project.id)] == sorted(
        [first.id, second.id]
    )
    assert len(store.list_task_lists(project.id)) == 2
    repeated = store.create_task_list("Repeated", repeatable=True, project_id="missing")
    assert store.get_project(repeated.project_id).name == "Repeatable"
