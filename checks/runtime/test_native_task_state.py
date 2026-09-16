import json

import pytest

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.models import TaskStatus
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.operations.durability.tombstones import read_tombstones


@pytest.mark.asyncio
async def test_actual_native_reconciliation_completion_edges_and_provenance(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    provider = NativeTaskProvider()
    first = await provider.create_task(
        title="First", author="creator", origin_harness="forged"
    )
    second = await provider.create_task(
        title="Second", dependencies=[{"depends_on_task_id": first.id}]
    )
    assert (await provider.get_task(second.id)).status is TaskStatus.BLOCKED
    changed = await provider.update_task(
        first.id, status="done", author="forged", origin_harness="forged"
    )
    assert changed._completed_edge and {row.id for row in changed._reconciled} == {
        first.id,
        second.id,
    }
    assert (
        changed.author == "creator"
        and changed.origin_harness == first.origin_harness != "forged"
    )
    assert (await provider.get_task(second.id)).status is TaskStatus.OPEN
    again = await provider.update_task(first.id, status="done")
    assert not again._completed_edge
    assert await provider.delete_task(first.id)
    loaded = await provider.get_task(second.id)
    assert loaded.dependencies == []
    assert {row["id"] for row in read_tombstones(tmp_path / "tasks")} == {first.id}


@pytest.mark.asyncio
async def test_derived_project_labels_and_comment_sidecars_survive_record_reload(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    hierarchy = HierarchyStore()
    project = hierarchy.create_project("Before")
    task_list = hierarchy.create_task_list("General", project_id=project.id)
    provider = NativeTaskProvider()
    task = await provider.create_task(
        title="Task", task_list_id=task_list.id, project="forged"
    )
    assert task.project == "Before"
    note = await provider.add_comment(task.id, "A note", "operator")
    assert (await provider.get_task(task.id))._comment_count == 1
    hierarchy.update_project(project.id, name="After")
    assert (await provider.get_task(task.id)).project == "After"
    assert await provider.delete_comment(task.id, note.id)
    assert provider._comments_path(task.id).read_text().strip() == "[]"
    assert (await provider.get_task(task.id))._comment_count == 0
    await provider.add_comment(task.id, "Retained sidecar")
    await provider.delete_task(task.id)
    assert len(await provider.get_comments(task.id)) == 1


@pytest.mark.asyncio
async def test_ordered_patch_validation_preserves_record_on_refusal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    provider = NativeTaskProvider()
    task = await provider.create_task(title="Original", exit_criteria=["Not complete"])
    before = provider._task_path(task.id).read_bytes()
    with pytest.raises(ValueError, match="unfinished exit criteria"):
        await provider.update_task(task.id, title="Changed", status="done")
    assert provider._task_path(task.id).read_bytes() == before
    completed = await provider.update_task(
        task.id, exit_criteria=[{"description": "Checked", "met": True}], status="done"
    )
    assert completed.status is TaskStatus.DONE
    provider._comments_path(task.id).write_text(
        json.dumps([17, {"id": "keep", "body": "Actual"}])
    )
    assert await provider.get_comments(task.id) == []
    assert await provider.delete_comment(task.id, "absent")
    assert [row.id for row in await provider.get_comments(task.id)] == ["keep"]
