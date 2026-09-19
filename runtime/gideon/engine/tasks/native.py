"""Native task records, ordered edits, graph reconciliation and comment sidecars."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.automation.workflows import pool
from gideon.core.config import loader as config_loader
from gideon.core.record_ids import record_path
from gideon.engine.tasks import reconcile
from gideon.engine.tasks.models import (
    TASK_FIELD_COERCERS,
    TERMINAL_STATUSES,
    Task,
    TaskComment,
    TaskDependency,
    TaskPriority,
    TaskStatus,
    WorkflowTaskBinding,
)
from gideon.engine.tasks.models import coerce_task_field as models_coerce
from gideon.engine.tasks.provider import TaskProvider

logger = logging.getLogger(__name__)
_IMMUTABLE_FIELDS = frozenset(
    {"id", "provider", "created_at", "author", "origin_harness"}
)
_CREATE_DEFAULTS = {
    "task_list_id": "",
    "title": "Untitled",
    "status": "open",
    "description": "",
    "assignee": "",
    "priority": "medium",
    "labels": [],
    "due": "",
    "order": 0.0,
    "exit_criteria": [],
    "action_plan": [],
    "notes": [],
    "research_notes": [],
    "execution_notes": [],
    "agent_instructions_template": "",
    "workflow_binding": None,
    "blocked_kind": "",
    "preview": "",
    "done_criterion": "",
    "evidence": [],
    "attempts": [],
    "author": "",
}


def config_dir() -> Path:
    return config_loader.config_dir()


def _tasks_dir() -> Path:
    return config_dir().joinpath("tasks")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _current_username() -> str:
    try:
        from gideon.cognition.identity import current_username

        return current_username()
    except Exception:
        return ""


def _current_origin_harness() -> str:
    try:
        from gideon.operations.durability.shards import machine_id

        return machine_id(config_dir())
    except Exception:
        return ""


def _record_task_tombstone(task_id: str) -> None:
    try:
        from gideon.operations.durability.tombstones import record_tombstone

        record_tombstone(_tasks_dir(), task_id, now=_now_iso())
    except Exception:
        pass


def _coerce_binding(raw: Any) -> WorkflowTaskBinding | None:
    if isinstance(raw, WorkflowTaskBinding):
        return raw
    return WorkflowTaskBinding.from_dict(raw) if isinstance(raw, dict) and raw else None


def create_provider(config: dict[str, Any] | None = None) -> "NativeTaskProvider":
    return NativeTaskProvider()


async def _fire_task_complete(task: Task) -> None:
    try:
        from gideon.engine.hooks import get_global_hook_store

        hooks = get_global_hook_store()
        if hooks is not None:
            binding = getattr(task, "workflow_binding", None)
            payload = pool.lifecycle_payload(
                task_id=task.id,
                title=task.title,
                status=task.status.value,
                run_id=getattr(binding, "run_id", "") or "",
                node_id=getattr(binding, "node_id", "") or "",
            )
            await hooks.fire(payload["event"], context=payload["context"])
    except Exception:
        logger.debug("TaskComplete hook fire failed", exc_info=True)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class TaskRecordFiles:
    provider: NativeTaskProvider

    def label(self, task_list_id, cache):
        if not task_list_id:
            return ""
        if cache is not None and task_list_id in cache:
            return cache[task_list_id]
        label = ""
        try:
            from gideon.engine.tasks.hierarchy import HierarchyStore

            hierarchy = HierarchyStore()
            task_list = hierarchy.get_task_list(task_list_id)
            project = hierarchy.get_project(task_list.project_id) if task_list else None
            if project:
                label = project.name
        except Exception:
            label = ""
        if cache is not None:
            cache[task_list_id] = label
        return label

    def read(self, path, cache):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["provider"] = self.provider.name
            task = Task.from_dict(data)
            task.project = self.provider._derive_project_label(
                task.task_list_id, cache=cache
            )
            setattr(task, "_comment_count", self.provider._comment_count(task.id))
            return task
        except Exception:
            return None

    def all(self):
        cache: dict[str, str] = {}
        return [
            row
            for path in sorted(self.provider._ensure_dir().glob("*.json"))
            if not path.name.startswith("_")
            and (row := self.provider._read_task(path, label_cache=cache))
        ]


@dataclass(frozen=True)
class TaskAdmission:
    task: Task
    tasks: dict[str, Task]

    def cycle(self):
        cycle = reconcile.would_create_cycle(
            self.tasks, self.task.id, self.task.prerequisite_ids()
        )
        if cycle:
            raise reconcile.DependencyCycleError(cycle)

    def completion(self):
        if not self.task.can_mark_complete():
            raise ValueError(
                "cannot complete: unfinished exit criteria — "
                + ", ".join(self.task.incomplete_exit_criteria())
            )
        blocked = reconcile.block_reason(self.task, self.tasks)
        if blocked["is_blocked"]:
            raise ValueError(
                "cannot complete: waiting on unfinished prerequisite — "
                + ", ".join(blocked["blocking_task_titles"])
            )


@dataclass(frozen=True)
class TaskMutation:
    provider: NativeTaskProvider

    def create(self, fields):
        identifier, now = f"t-{uuid.uuid4().hex[:8]}", _now_iso()
        values = {
            name: models_coerce(name, fields.get(name, default), strict=True)
            for name, default in _CREATE_DEFAULTS.items()
        }
        values.update(
            id=identifier,
            provider=self.provider.name,
            project=self.provider._derive_project_label(values["task_list_id"]),
            dependencies=models_coerce(
                "dependencies",
                fields.get("dependencies", fields.get("depends_on", [])),
                strict=True,
            ),
            author=values["author"] or _current_username(),
            origin_harness=_current_origin_harness(),
            created_at=now,
            updated_at=now,
        )
        task = Task(**values)
        tasks = self.provider._task_map()
        admission = TaskAdmission(task, {**tasks, task.id: task})
        admission.cycle()
        if task.status == TaskStatus.DONE:
            admission.completion()
        tasks[task.id] = task
        reconcile.classify_manual_block(task, tasks)
        self.provider._write_task(task)
        for changed in reconcile.reconcile_blocked_status(tasks, task.id):
            self.provider._write_task(changed)
        return task

    def update(self, identifier, fields):
        tasks = self.provider._task_map()
        task = tasks.get(identifier)
        if not task:
            return None
        previous_status, changed_graph = task.status, False
        previous = previous_status.value
        admission = TaskAdmission(task, tasks)
        for name, value in fields.items():
            if name == "status":
                status = models_coerce("status", value, strict=True)
                if status == TaskStatus.DONE:
                    admission.completion()
                task.status, changed_graph = status, True
            elif name in ("dependencies", "depends_on"):
                task.dependencies = self.provider._coerce_dependencies(value)
                changed_graph = True
            elif name == "priority":
                task.priority = TaskPriority.normalize(value)
            elif name == "project" or name in _IMMUTABLE_FIELDS:
                continue
            elif name not in TASK_FIELD_COERCERS:
                logger.debug(
                    "update_task ignoring unknown field %r on %s", name, identifier
                )
            else:
                setattr(task, name, models_coerce(name, value, strict=True))
        if "task_list_id" in fields:
            task.project = self.provider._derive_project_label(task.task_list_id)
        if (
            "status" in fields
            and previous_status in TERMINAL_STATUSES
            and task.status == TaskStatus.OPEN
            and task.project == "Repeatable"
        ):
            task.reset_for_repeat()
            changed_graph = True
        admission.cycle()
        task.updated_at = _now_iso()
        if "status" in fields:
            reconcile.classify_manual_block(task, tasks)
        changed = [task]
        if changed_graph:
            changed.extend(
                row
                for row in reconcile.reconcile_blocked_status(tasks, task.id)
                if row.id != task.id
            )
        for row in changed:
            self.provider._write_task(row)
        setattr(task, "_reconciled", changed)
        setattr(
            task,
            "_completed_edge",
            pool.should_fire_completion(previous, task.status.value),
        )
        return task

    def delete(self, identifier):
        path = self.provider._task_path(identifier)
        if not path.exists():
            return False
        path.unlink()
        _record_task_tombstone(identifier)
        tasks = self.provider._task_map()
        for task in tasks.values():
            retained = [
                edge
                for edge in task.dependencies
                if edge.depends_on_task_id != identifier
            ]
            if len(retained) != len(task.dependencies):
                task.dependencies = retained
                self.provider._write_task(task)
        for key in list(tasks):
            for changed in reconcile.reconcile_blocked_status(tasks, key):
                self.provider._write_task(changed)
        return True


@dataclass(frozen=True)
class TaskComments:
    provider: NativeTaskProvider
    task_id: str

    def path(self):
        return self.provider._comments_path(self.task_id)

    def count(self):
        try:
            path = self.path()
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            return len(data) if isinstance(data, list) else 0
        except Exception:
            return 0

    def read(self):
        path = self.path()
        if not path.exists():
            return []
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            return [
                TaskComment(
                    id=row["id"],
                    task_id=self.task_id,
                    **{
                        name: row.get(name, "")
                        for name in ("author", "body", "created_at")
                    },
                )
                for row in rows
            ]
        except Exception:
            return []

    def append(self, body, author):
        if not self.provider._task_path(self.task_id).exists():
            return None
        path = self.path()
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            rows = []
        row = {
            "id": f"c-{uuid.uuid4().hex[:8]}",
            "author": author or _current_username(),
            "body": body,
            "created_at": _now_iso(),
        }
        rows.append(row)
        _write_json(path, rows)
        return TaskComment(task_id=self.task_id, **row)

    def delete(self, comment_id):
        path = self.path()
        if not path.exists():
            return False
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(rows, list):
            return False
        retained = [
            row for row in rows if isinstance(row, dict) and row.get("id") != comment_id
        ]
        if len(retained) == len(rows):
            return False
        _write_json(path, retained)
        return True


class NativeTaskProvider(TaskProvider):
    @property
    def name(self) -> str:
        return "native"

    def _ensure_dir(self) -> Path:
        path = _tasks_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _task_path(self, task_id: str) -> Path:
        return record_path(self._ensure_dir(), task_id, kind="task_id")

    def _comments_path(self, task_id: str) -> Path:
        return record_path(
            self._ensure_dir(), task_id, prefix="_comments_", kind="task_id"
        )

    def _comment_count(self, task_id: str) -> int:
        return TaskComments(self, task_id).count()

    def _read_task(self, path: Path, label_cache: dict | None = None) -> Task | None:
        return TaskRecordFiles(self).read(path, label_cache)

    def _write_task(self, task: Task) -> None:
        _write_json(self._task_path(task.id), task.to_dict())

    def _all_tasks(self) -> list[Task]:
        return TaskRecordFiles(self).all()

    def _task_map(self) -> dict[str, Task]:
        return {row.id: row for row in self._all_tasks()}

    def _derive_project_label(
        self, task_list_id: str, cache: dict | None = None
    ) -> str:
        return TaskRecordFiles(self).label(task_list_id, cache)

    @staticmethod
    def _coerce_dependencies(value: Any) -> list[TaskDependency]:
        values = (
            [value] if isinstance(value, (str, dict, TaskDependency)) else value or []
        )
        edges = []
        for item in values:
            if isinstance(item, TaskDependency):
                edges.append(item)
            elif isinstance(item, dict):
                edges.append(TaskDependency.from_dict(item))
            elif isinstance(item, str) and item.strip():
                edges.append(TaskDependency(item.strip()))
        return edges

    async def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
        task_list_id: str | None = None,
    ) -> tuple[list[Task], int]:
        def select():
            records = [
                row
                for row in self._all_tasks()
                if (not status or row.status.value == status)
                and (not assignee or row.assignee == assignee)
                and (not project or row.project == project)
                and (not task_list_id or row.task_list_id == task_list_id)
            ]
            return records[offset : offset + limit], len(records)

        return await asyncio.to_thread(select)

    async def get_task(self, task_id: str) -> Task | None:
        return await asyncio.to_thread(self._read_task, self._task_path(task_id))

    async def create_task(self, **fields: Any) -> Task:
        return await asyncio.to_thread(TaskMutation(self).create, fields)

    async def update_task(self, task_id: str, **fields: Any) -> Task | None:
        edited = await asyncio.to_thread(TaskMutation(self).update, task_id, fields)
        if edited is not None and getattr(edited, "_completed_edge", False):
            from gideon.extensions.apps.app_events import TASK_COMPLETED, emit

            emit(TASK_COMPLETED, {"task_id": edited.id, "status": edited.status.value})
            await _fire_task_complete(edited)
        return edited

    async def delete_task(self, task_id: str) -> bool:
        return await asyncio.to_thread(TaskMutation(self).delete, task_id)

    def graph(self) -> dict[str, Any]:
        tasks = self._task_map()
        edges = [
            {
                "from": key,
                "to": edge.depends_on_task_id,
                "type": edge.dependency_type.value,
            }
            for key, task in tasks.items()
            for edge in task.dependencies
            if edge.depends_on_task_id in tasks
        ]
        return {
            "tasks": [row.to_dict() for row in tasks.values()],
            "edges": edges,
            "analysis": reconcile.analyze(tasks).to_dict(),
        }

    async def get_comments(self, task_id: str) -> list[TaskComment]:
        return await asyncio.to_thread(TaskComments(self, task_id).read)

    async def add_comment(
        self, task_id: str, body: str, author: str = ""
    ) -> TaskComment | None:
        return await asyncio.to_thread(TaskComments(self, task_id).append, body, author)

    async def delete_comment(self, task_id: str, comment_id: str) -> bool:
        return await asyncio.to_thread(TaskComments(self, task_id).delete, comment_id)


Provider = NativeTaskProvider
