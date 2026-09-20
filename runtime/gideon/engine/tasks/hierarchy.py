"""Project and task-list records, layout migration and deletion lineage."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from gideon.core.config import loader as config_loader
from gideon.core.record_ids import is_safe_record_id, record_path
from gideon.engine.tasks.models import BUILTIN_PROJECTS, Project, TaskList

logger = logging.getLogger(__name__)


def validate_container_name(name: str) -> str:
    selected = str(name or "").strip()
    if not selected:
        raise ValueError("container name is required")
    return selected


def config_dir() -> Path:
    return config_loader.config_dir()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _current_origin_harness() -> str:
    try:
        from gideon.operations.durability.shards import machine_id

        return machine_id(config_dir())
    except Exception:
        return ""


def _directory(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_entity(path, entity):
    try:
        return entity.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _write_entity(path, entity):
    path.write_text(json.dumps(entity.to_dict(), indent=2), encoding="utf-8")


def _entity_identity(prefix):
    now = _now_iso()
    return {
        "id": f"{prefix}-{uuid.uuid4().hex[:8]}",
        "created_at": now,
        "updated_at": now,
    }


@dataclass(frozen=True)
class ProjectMigration:
    store: HierarchyStore

    def apply(self):
        import shutil

        for path in self.store._projects_dir().glob("*.json"):
            if path.is_file():
                path.unlink(missing_ok=True)
        legacy = self.store._base() / "projects"
        if legacy.is_dir():
            for path in sorted(legacy.glob("*.json")):
                project = self.store._read_project(path)
                if project is None:
                    continue
                if not is_safe_record_id(project.id):
                    logger.warning(
                        "skipping legacy project %r during layout migration: its id is not a single path segment",
                        project.id,
                    )
                    continue
                if not self.store._project_path(project.id).exists():
                    self.store._write_project(project)
            shutil.rmtree(legacy, ignore_errors=True)
        for project in self.store._all_projects_raw():
            if project.name == "Chore" and not any(
                row.name == "Personal" for row in self.store._all_projects_raw()
            ):
                project.name, project.is_builtin = "Personal", True
                self.store._write_project(project)


@dataclass(frozen=True)
class DeletionLineage:
    store: HierarchyStore

    def project(self, identifier):
        try:
            from gideon.operations.durability.tombstones import record_tombstone

            root = self.store._projects_dir()
            tree = self.store._project_dir(identifier)
            if not tree.is_dir():
                return
            now = _now_iso()
            for path in sorted(tree.rglob("*.json")):
                relative = path.relative_to(tree).as_posix()
                if relative.startswith("worktrees/") or "/worktrees/" in relative:
                    continue
                record_tombstone(
                    root,
                    path.relative_to(root).as_posix().removesuffix(".json"),
                    now=now,
                )
        except Exception:
            pass

    def task_list(self, identifier):
        try:
            from gideon.operations.durability.tombstones import record_tombstone

            row = (
                self.store._list_path(identifier)
                .relative_to(self.store._base())
                .as_posix()
            )
            record_tombstone(
                self.store._base(), row.removesuffix(".json"), now=_now_iso()
            )
        except Exception:
            pass


@dataclass(frozen=True)
class ProjectPatch:
    store: HierarchyStore
    project: Project
    fields: dict

    def apply(self):
        row, fields = self.project, self.fields
        if "name" in fields:
            name = str(fields["name"]).strip()
            if not name:
                raise ValueError("project name cannot be empty")
            if row.is_builtin_project() and name != row.name:
                raise ValueError(f"the built-in project '{row.name}' cannot be renamed")
            other = self.store.get_project_by_name(name)
            if other and other.id != row.id:
                raise ValueError(f"a project named '{name}' already exists")
            row.name = name
        setters = {
            "agent_instructions_template": lambda value: value,
            "brief": lambda value: str(value or "").strip(),
            "workspace_dir": self.store._validate_workspace_dir,
            "status": self.status,
            "name_locked": bool,
        }
        for name, normalize in setters.items():
            if name in fields:
                setattr(row, name, normalize(fields[name]))
        row.updated_at = _now_iso()
        return row

    @staticmethod
    def status(value):
        cleaned = str(value or "").strip()
        if cleaned not in ("active", "archived"):
            raise ValueError("status must be 'active' or 'archived'")
        return cleaned


@dataclass(frozen=True)
class ListDestination:
    store: HierarchyStore
    project_id: str
    project_name: str
    repeatable: bool

    def resolve(self):
        if self.repeatable:
            return self.store.find_or_create_project("Repeatable")
        if not self.project_id:
            return self.store.find_or_create_project(self.project_name or "Personal")
        project = self.store.get_project(self.project_id)
        if project is None:
            raise ValueError(f"no project with id '{self.project_id}'")
        return project


@dataclass(frozen=True)
class TaskDestination:
    store: HierarchyStore
    task_list_id: str
    project_id: str

    def validate(self):
        project = None
        if self.project_id:
            project = self.store.get_project(self.project_id)
            if project is None:
                raise ValueError(f"no project with id '{self.project_id}'")
        task_list = None
        if self.task_list_id:
            task_list = self.store.get_task_list(self.task_list_id)
            if task_list is None:
                raise ValueError(f"no task list with id '{self.task_list_id}'")
            parent = self.store.get_project(task_list.project_id)
            if parent is None:
                raise ValueError(
                    f"task list '{self.task_list_id}' has no existing parent project"
                )
            if project is not None and task_list.project_id != project.id:
                raise ValueError(
                    f"task list '{self.task_list_id}' does not belong to project "
                    f"'{self.project_id}'"
                )
        return task_list, project

    def resolve(self):
        task_list, project = self.validate()
        if task_list is not None or project is None:
            return self.task_list_id
        candidates = sorted(
            (
                row
                for row in self.store.list_task_lists(project.id)
                if row.name == "General"
            ),
            key=lambda row: row.created_at or "",
        )
        if candidates:
            return candidates[0].id
        return self.store.create_task_list(name="General", project_id=project.id).id


class HierarchyStore:
    def _base(self) -> Path:
        return config_dir().joinpath("tasks")

    def _projects_dir(self) -> Path:
        return _directory(config_dir() / "projects")

    def _lists_dir(self) -> Path:
        return _directory(self._base() / "task_lists")

    def _project_dir(self, project_id: str) -> Path:
        return record_path(
            self._projects_dir(), project_id, suffix="", kind="project_id"
        )

    def _project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id).joinpath("project.json")

    def context_dir(self, project_id: str) -> Path:
        return _directory(self._project_dir(project_id) / "context")

    def worktrees_dir(self, project_id: str) -> Path:
        return _directory(self._project_dir(project_id) / "worktrees")

    def _read_project(self, path: Path) -> Project | None:
        return _read_entity(path, Project)

    def _write_project(self, project: Project) -> None:
        _directory(self._project_dir(project.id))
        self.context_dir(project.id)
        _write_entity(self._project_path(project.id), project)

    def migrate_layout(self) -> None:
        ProjectMigration(self).apply()

    def ensure_defaults(self) -> None:
        self.migrate_layout()
        names = {row.name for row in self._all_projects_raw()}
        for name in BUILTIN_PROJECTS:
            if name not in names:
                self._write_project(
                    Project(
                        **_entity_identity("p"),
                        name=name,
                        is_builtin=True,
                        origin_harness=_current_origin_harness(),
                    )
                )

    def _all_projects_raw(self) -> list[Project]:
        return [
            project
            for path in sorted(self._projects_dir().iterdir())
            if path.is_dir() and (project := self._read_project(path / "project.json"))
        ]

    def list_projects(self) -> list[Project]:
        self.ensure_defaults()
        return sorted(
            self._all_projects_raw(),
            key=lambda row: (not row.is_builtin_project(), row.name.lower()),
        )

    def get_project(self, project_id: str) -> Project | None:
        return self._read_project(self._project_path(project_id))

    def get_project_by_name(self, name: str) -> Project | None:
        return next((row for row in self._all_projects_raw() if row.name == name), None)

    def find_or_create_project(self, name: str) -> Project:
        selected = name.strip() or "Personal"
        existing = self.get_project_by_name(selected)
        if existing:
            return existing
        project = Project(
            **_entity_identity("p"),
            name=selected,
            is_builtin=selected in BUILTIN_PROJECTS,
            origin_harness=_current_origin_harness(),
        )
        self._write_project(project)
        return project

    def _validate_workspace_dir(self, workspace_dir: str) -> str:
        selected = str(workspace_dir or "").strip()
        if selected:
            from gideon.automation.loop.validation import workspace_write_target_errors

            errors = workspace_write_target_errors(selected)
            if errors:
                raise ValueError(errors[0])
        return selected

    def create_project(
        self,
        name: str,
        agent_instructions_template: str = "",
        *,
        workspace_dir: str = "",
        name_locked: bool = False,
        brief: str = "",
    ) -> Project:
        selected = validate_container_name(name)
        if self.get_project_by_name(selected):
            raise ValueError(f"a project named '{selected}' already exists")
        project = Project(
            **_entity_identity("p"),
            name=selected,
            is_builtin=selected in BUILTIN_PROJECTS,
            workspace_dir=self._validate_workspace_dir(workspace_dir),
            name_locked=bool(name_locked),
            agent_instructions_template=agent_instructions_template,
            brief=str(brief or "").strip(),
            origin_harness=_current_origin_harness(),
        )
        self._write_project(project)
        return project

    def update_project(self, project_id: str, **fields) -> Project | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        updated = ProjectPatch(self, project, fields).apply()
        self._write_project(updated)
        return updated

    def delete_project(self, project_id: str) -> bool:
        import shutil

        project = self.get_project(project_id)
        if project is None:
            return False
        if project.name in BUILTIN_PROJECTS:
            raise ValueError(f"the built-in project '{project.name}' cannot be deleted")
        self.delete_task_lists(
            row.id for row in self.list_task_lists(project_id=project_id)
        )
        self._record_project_subtree_tombstones(project_id)
        shutil.rmtree(self._project_dir(project_id), ignore_errors=True)
        return True

    def _record_project_subtree_tombstones(self, project_id: str) -> None:
        DeletionLineage(self).project(project_id)

    def _list_path(self, list_id: str) -> Path:
        return record_path(self._lists_dir(), list_id, kind="list_id")

    def _read_list(self, path: Path) -> TaskList | None:
        return _read_entity(path, TaskList)

    def _write_list(self, tl: TaskList) -> None:
        _write_entity(self._list_path(tl.id), tl)

    def _all_lists_raw(self) -> list[TaskList]:
        return [
            row
            for path in sorted(self._lists_dir().glob("*.json"))
            if (row := self._read_list(path))
        ]

    def list_task_lists(self, project_id: str | None = None) -> list[TaskList]:
        return sorted(
            (
                row
                for row in self._all_lists_raw()
                if not project_id or row.project_id == project_id
            ),
            key=lambda row: row.name.lower(),
        )

    def get_task_list(self, list_id: str) -> TaskList | None:
        return self._read_list(self._list_path(list_id))

    def task_destination(
        self, *, task_list_id: object = "", project_id: object = ""
    ) -> TaskDestination:
        return TaskDestination(
            self,
            str(task_list_id or "").strip(),
            str(project_id or "").strip(),
        )

    def create_task_list(
        self,
        name: str,
        *,
        project_id: str = "",
        project_name: str = "",
        repeatable: bool = False,
        agent_instructions_template: str = "",
    ) -> TaskList:
        selected = validate_container_name(name)
        self.ensure_defaults()
        project = ListDestination(self, project_id, project_name, repeatable).resolve()
        if any(
            row.name == selected for row in self.list_task_lists(project_id=project.id)
        ):
            raise ValueError(
                f"a task list named '{selected}' already exists in this project"
            )
        row = TaskList(
            **_entity_identity("tl"),
            name=selected,
            project_id=project.id,
            agent_instructions_template=agent_instructions_template,
        )
        self._write_list(row)
        return row

    def update_task_list(self, list_id: str, **fields) -> TaskList | None:
        row = self.get_task_list(list_id)
        if row is None:
            return None
        if "name" in fields:
            row.name = str(fields["name"]).strip()
            if not row.name:
                raise ValueError("task list name cannot be empty")
        if fields.get("project_id"):
            destination = ListDestination(
                self, fields["project_id"], "", False
            ).resolve()
            row.project_id = destination.id
        if "agent_instructions_template" in fields:
            row.agent_instructions_template = fields["agent_instructions_template"]
        row.updated_at = _now_iso()
        self._write_list(row)
        return row

    def delete_task_list(self, list_id: str) -> bool:
        path = self._list_path(list_id)
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        self._record_list_tombstone(list_id)
        return True

    def delete_task_lists(self, list_ids) -> int:
        return sum(
            self.delete_task_list(list_id) for list_id in dict.fromkeys(list_ids)
        )

    def _record_list_tombstone(self, list_id: str) -> None:
        DeletionLineage(self).task_list(list_id)
