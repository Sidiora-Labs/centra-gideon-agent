"""Tests for the Project / TaskList hierarchy store."""

from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.hierarchy_handlers import register_hierarchy_routes
from gideon.engine.tasks.models import (
    BUILTIN_PROJECTS,
    Project,
    Task,
    TaskList,
    TaskStatus,
)
from gideon.engine.tasks.native import NativeTaskProvider


@pytest.fixture()
def store(tmp_path):
    with patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path):
        yield HierarchyStore()


class TestDefaults:
    def test_defaults_seeded(self, store):
        projects = store.list_projects()
        names = {p.name for p in projects}
        assert "Personal" in names
        assert "Repeatable" in names
        assert all(
            p.is_builtin_project() for p in projects if p.name in BUILTIN_PROJECTS
        )

    def test_defaults_idempotent(self, store):
        store.ensure_defaults()
        store.ensure_defaults()
        personals = [p for p in store.list_projects() if p.name == "Personal"]
        assert len(personals) == 1

    def test_default_project_undeletable(self, store):
        store.ensure_defaults()
        personal = store.get_project_by_name("Personal")
        with pytest.raises(ValueError, match="cannot be deleted"):
            store.delete_project(personal.id)

    def test_guard_refusals_say_builtin_not_the_default(self, store):
        store.ensure_defaults()
        for name in BUILTIN_PROJECTS:
            p = store.get_project_by_name(name)
            with pytest.raises(ValueError) as del_err:
                store.delete_project(p.id)
            assert f"built-in project '{name}' cannot be deleted" in str(del_err.value)
            assert "default" not in str(del_err.value)
            with pytest.raises(ValueError) as ren_err:
                store.update_project(p.id, name="Something Else")
            assert f"built-in project '{name}' cannot be renamed" in str(ren_err.value)
            assert "default" not in str(ren_err.value)

    def test_default_project_rename_refused_and_no_duplicate(self, store):
        store.ensure_defaults()
        personal = store.get_project_by_name("Personal")
        with pytest.raises(ValueError, match="cannot be renamed"):
            store.update_project(personal.id, name="Renamed")
        personals = [p for p in store.list_projects() if p.name == "Personal"]
        assert len(personals) == 1
        assert personals[0].id == personal.id
        assert store.get_project_by_name("Renamed") is None

    def test_default_project_non_name_update_still_works(self, store):
        store.ensure_defaults()
        personal = store.get_project_by_name("Personal")
        u = store.update_project(
            personal.id, brief="Catch-all", workspace_dir="/tmp/x", status="archived"
        )
        assert u.name == "Personal"
        assert (
            u.brief == "Catch-all"
            and u.workspace_dir == "/tmp/x"
            and u.status == "archived"
        )
        assert store.update_project(personal.id, name="Personal").name == "Personal"

    def test_stray_default_flagged_project_is_deletable(self, store):
        store.ensure_defaults()
        p = store.create_project("Leftover")
        p.is_builtin = True
        store._write_project(p)
        assert store.get_project(p.id).is_builtin_project() is True
        assert store.delete_project(p.id) is True
        assert store.get_project(p.id) is None
        personal = store.get_project_by_name("Personal")
        with pytest.raises(ValueError, match="cannot be deleted"):
            store.delete_project(personal.id)


class TestProjectCrud:
    def test_create_and_get(self, store):
        p = store.create_project("Website")
        assert p.name == "Website"
        assert not p.is_builtin
        assert store.get_project(p.id).name == "Website"

    def test_create_duplicate_name_rejected(self, store):
        store.create_project("Website")
        with pytest.raises(ValueError, match="already exists"):
            store.create_project("Website")

    def test_create_empty_name_rejected(self, store):
        with pytest.raises(ValueError, match="required"):
            store.create_project("   ")

    def test_update_name(self, store):
        p = store.create_project("Old")
        updated = store.update_project(p.id, name="New")
        assert updated.name == "New"
        assert store.get_project(p.id).name == "New"

    def test_update_to_duplicate_name_rejected(self, store):
        store.create_project("A")
        b = store.create_project("B")
        with pytest.raises(ValueError, match="already exists"):
            store.update_project(b.id, name="A")

    def test_delete_custom_project(self, store):
        p = store.create_project("Temp")
        assert store.delete_project(p.id) is True
        assert store.get_project(p.id) is None

    def test_find_or_create(self, store):
        a = store.find_or_create_project("Reused")
        b = store.find_or_create_project("Reused")
        assert a.id == b.id


class TestProjectEntity:
    """The first-class Project: context dir, workspace binding, new fields."""

    def test_context_dir_created_with_project(self, store, tmp_path):
        p = store.create_project("Ctx")
        ctx = store.context_dir(p.id)
        assert ctx.is_dir()
        assert ctx == tmp_path / "projects" / p.id / "context"

    def test_project_json_lives_in_per_project_dir(self, store, tmp_path):
        p = store.create_project("Layout")
        assert (tmp_path / "projects" / p.id / "project.json").is_file()

    def test_create_with_workspace_dir(self, store):
        p = store.create_project("Bound", workspace_dir="/tmp/repo")
        assert p.workspace_dir == "/tmp/repo"
        assert store.get_project(p.id).workspace_dir == "/tmp/repo"
        assert store.create_project("Free").workspace_dir == ""

    def test_update_workspace_and_status_and_lock(self, store):
        p = store.create_project("W")
        u = store.update_project(
            p.id, workspace_dir="/tmp/x", status="archived", name_locked=True
        )
        assert (
            u.workspace_dir == "/tmp/x"
            and u.status == "archived"
            and u.name_locked is True
        )
        re = store.get_project(p.id)
        assert re.status == "archived" and re.name_locked is True

    def test_update_invalid_status_rejected(self, store):
        p = store.create_project("S")
        with pytest.raises(ValueError, match="status must be"):
            store.update_project(p.id, status="bogus")

    def test_delete_removes_project_dir(self, store, tmp_path):
        p = store.create_project("Gone")
        store.context_dir(p.id)
        pdir = tmp_path / "projects" / p.id
        assert pdir.is_dir()
        assert store.delete_project(p.id) is True
        assert not pdir.exists()

    def test_worktrees_dir(self, store, tmp_path):
        p = store.create_project("WT")
        wt = store.worktrees_dir(p.id)
        assert wt.is_dir() and wt == tmp_path / "projects" / p.id / "worktrees"


class TestMigration:
    """One-time migration to the projects/<id>/ layout (clean break, idempotent)."""

    def test_migrates_old_store_deletes_legacy_orphans_renames_chore(self, tmp_path):
        import json

        (tmp_path / "projects").mkdir(parents=True)
        (tmp_path / "projects" / "deadbeef.json").write_text(
            json.dumps(
                {"id": "deadbeef", "name": "Use below report", "phases": [1, 2, 3]}
            )
        )
        (tmp_path / "tasks" / "projects").mkdir(parents=True)
        (tmp_path / "tasks" / "projects" / "chore.json").write_text(
            json.dumps({"id": "chore", "name": "Chore", "is_builtin": True})
        )
        (tmp_path / "tasks" / "projects" / "p-keep0001.json").write_text(
            json.dumps({"id": "p-keep0001", "name": "Real Work"})
        )
        with patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path):
            store = HierarchyStore()
            names = {p.name for p in store.list_projects()}
        assert "Use below report" not in names
        assert not list((tmp_path / "projects").glob("*.json"))
        assert not (tmp_path / "tasks" / "projects").exists()
        assert "Personal" in names and "Real Work" in names and "Chore" not in names
        assert (tmp_path / "projects" / "p-keep0001" / "project.json").is_file()

    def test_migration_idempotent(self, tmp_path):
        with patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path):
            store = HierarchyStore()
            store.list_projects()
            before = {p.id for p in store.list_projects()}
            store.migrate_layout()
            after = {p.id for p in store.list_projects()}
        assert before == after


class TestTaskListRouting:
    def test_repeatable_routes_to_repeatable_project(self, store):
        tl = store.create_task_list("Weekly review", repeatable=True)
        repeatable = store.get_project_by_name("Repeatable")
        assert tl.project_id == repeatable.id

    def test_explicit_project_id(self, store):
        p = store.create_project("Proj")
        tl = store.create_task_list("List", project_id=p.id)
        assert tl.project_id == p.id

    def test_unknown_project_id_rejected(self, store):
        with pytest.raises(ValueError, match="no project with id"):
            store.create_task_list("List", project_id="p-nope")

    def test_project_name_find_or_create(self, store):
        tl = store.create_task_list("List", project_name="Fresh")
        fresh = store.get_project_by_name("Fresh")
        assert fresh is not None
        assert tl.project_id == fresh.id

    def test_no_project_routes_to_personal(self, store):
        tl = store.create_task_list("Orphan list")
        personal = store.get_project_by_name("Personal")
        assert tl.project_id == personal.id

    def test_empty_name_rejected(self, store):
        with pytest.raises(ValueError, match="required"):
            store.create_task_list("  ")

    def test_duplicate_name_in_same_project_rejected(self, store):
        p = store.create_project("Proj")
        store.create_task_list("Dup", project_id=p.id)
        with pytest.raises(ValueError, match="already exists in this project"):
            store.create_task_list("Dup", project_id=p.id)

    def test_same_name_in_different_projects_allowed(self, store):
        a = store.create_project("A")
        b = store.create_project("B")
        store.create_task_list("Shared", project_id=a.id)
        tl = store.create_task_list("Shared", project_id=b.id)
        assert tl.project_id == b.id


class TestTaskListCrud:
    def test_list_by_project(self, store):
        p = store.create_project("Proj")
        store.create_task_list("L1", project_id=p.id)
        store.create_task_list("L2", project_id=p.id)
        store.create_task_list("Other")
        assert len(store.list_task_lists(project_id=p.id)) == 2

    def test_update_moves_to_another_project(self, store):
        a = store.create_project("A")
        b = store.create_project("B")
        tl = store.create_task_list("L", project_id=a.id)
        store.update_task_list(tl.id, project_id=b.id)
        assert store.get_task_list(tl.id).project_id == b.id

    def test_delete(self, store):
        tl = store.create_task_list("L")
        assert store.delete_task_list(tl.id) is True
        assert store.get_task_list(tl.id) is None

    def test_delete_project_cascades_lists(self, store):
        p = store.create_project("Proj")
        tl = store.create_task_list("L", project_id=p.id)
        store.delete_project(p.id)
        assert store.get_task_list(tl.id) is None

    @pytest.mark.asyncio
    async def test_delete_route_cascades_only_tasks_in_the_deleted_list(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gideon.engine.tasks.hierarchy.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr("gideon.engine.tasks.native.config_dir", lambda: tmp_path)
        monkeypatch.setattr(registry, "_providers", {"native": NativeTaskProvider()})
        hierarchy = HierarchyStore()
        project = hierarchy.create_project("Proj")
        removed_list = hierarchy.create_task_list("Removed", project_id=project.id)
        kept_list = hierarchy.create_task_list("Kept", project_id=project.id)
        removed_task = await registry.create_task(
            title="Removed task", task_list_id=removed_list.id
        )
        kept_task = await registry.create_task(
            title="Kept task", task_list_id=kept_list.id
        )
        app = web.Application()
        register_hierarchy_routes(app)

        async with TestClient(TestServer(app)) as client:
            response = await client.delete(f"/api/task-lists/{removed_list.id}")

        assert response.status == 200
        assert hierarchy.get_task_list(removed_list.id) is None
        assert hierarchy.get_task_list(kept_list.id) is not None
        assert await registry.get_task(removed_task.id) is None
        assert await registry.get_task(kept_task.id) is not None


class TestGeneralAutoAttach:
    def test_attaches_to_oldest_general_among_grandfathered_duplicates(self, store):
        from gideon.engine.tasks.handlers import _attach_project_general_list

        p = store.create_project("Legacy")
        older = TaskList(
            id="tl-older",
            name="General",
            project_id=p.id,
            created_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        )
        newer = TaskList(
            id="tl-a-newer",
            name="General",
            project_id=p.id,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._write_list(older)
        store._write_list(newer)
        body: dict = {"project_id": p.id}
        _attach_project_general_list(body)
        assert body["task_list_id"] == older.id

    def test_unknown_task_list_is_rejected(self, store):
        with pytest.raises(ValueError, match="no task list with id"):
            store.task_destination(task_list_id="tl-missing").validate()

    def test_task_list_with_missing_parent_project_is_rejected(self, store):
        store._write_list(TaskList(id="tl-orphan", name="Lost", project_id="p-missing"))
        with pytest.raises(ValueError, match="no existing parent project"):
            store.task_destination(task_list_id="tl-orphan").validate()

    def test_task_list_and_project_must_agree(self, store):
        first = store.create_project("First")
        second = store.create_project("Second")
        task_list = store.create_task_list("List", project_id=first.id)
        with pytest.raises(ValueError, match="does not belong"):
            store.task_destination(
                task_list_id=task_list.id, project_id=second.id
            ).validate()

    def test_unknown_project_is_rejected_before_general_list_creation(self, store):
        with pytest.raises(ValueError, match="no project with id"):
            store.task_destination(project_id="p-missing").resolve()
        assert store.list_task_lists(project_id="p-missing") == []


class TestModelSerialization:
    def test_project_roundtrip(self):
        p = Project(id="p1", name="X", created_at="t", updated_at="t")
        assert Project.from_dict(p.to_dict()).name == "X"

    def test_default_name_implies_default_flag(self):
        p = Project.from_dict({"id": "p1", "name": "Personal"})
        assert p.is_builtin_project() is True

    def test_tasklist_roundtrip(self):
        tl = TaskList(id="tl1", name="L", project_id="p1")
        assert TaskList.from_dict(tl.to_dict()).project_id == "p1"

    def test_repeat_reset_clears_run_state_but_preserves_task_definition(self):
        task = Task(
            id="t1",
            title="Weekly review",
            status=TaskStatus.DONE,
            description="Review the week",
            exit_criteria=[
                {"description": "Reviewed", "status": "complete", "comment": "yes"}
            ],
            action_plan=[{"content": "Read notes", "completed": True}],
            notes=[{"content": "Keep this"}],
            research_notes=[{"content": "And this"}],
            execution_notes=[{"content": "Last run"}],
            blocked_reason_kind="manual",
            blocked_kind="input",
            preview="old preview",
            done_criterion="pytest -q",
            evidence=[{"kind": "gate", "ref": "old"}],
            attempts=[{"status": "passed"}],
        )

        task.reset_for_repeat()

        assert task.status is TaskStatus.OPEN
        assert task.exit_criteria == [
            {
                "description": "Reviewed",
                "status": "incomplete",
                "comment": "",
                "met": False,
            }
        ]
        assert task.action_plan[0]["completed"] is False
        assert (
            task.execution_notes == [] and task.evidence == [] and task.attempts == []
        )
        assert task.blocked_reason_kind == "" and task.blocked_kind == ""
        assert task.preview == ""
        assert task.description == "Review the week"
        assert task.notes and task.research_notes and task.done_criterion == "pytest -q"


class TestWorkspaceBindGuard:
    """A bound workspace becomes a WRITE target for generated agent files and the cwd of an
    unsandboxed worker, so create/update refuse an unsafe path at bind time (#358)."""

    _UNSAFE = [
        "/",
        "~",
        "relative/dir",
        "~/.ssh",
    ]

    @pytest.mark.parametrize("bad", _UNSAFE)
    def test_create_refuses_unsafe_workspace(self, store, bad):
        with pytest.raises(ValueError):
            store.create_project("Bound", workspace_dir=bad)
        assert store.get_project_by_name("Bound") is None

    @pytest.mark.parametrize("bad", _UNSAFE)
    def test_update_refuses_unsafe_workspace(self, store, bad):
        p = store.create_project("Bound")
        with pytest.raises(ValueError):
            store.update_project(p.id, workspace_dir=bad)
        assert store.get_project(p.id).workspace_dir == ""

    def test_safe_absolute_workspace_still_binds(self, store, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        p = store.create_project("Bound", workspace_dir=str(d))
        assert p.workspace_dir == str(d)
        assert store.update_project(p.id, workspace_dir="").workspace_dir == ""
