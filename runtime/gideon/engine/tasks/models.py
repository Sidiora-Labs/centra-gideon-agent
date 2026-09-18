"""Task wire records and shared write-admission/read-salvage normalization."""

import enum
from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _enum_value(kind, value, fallback):
    try:
        return kind(value)
    except ValueError:
        return fallback


class TaskStatus(enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


STARTABLE_STATUSES = (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)
"""Work a scheduler may offer: nobody is holding it and it still owes work."""

HELD_STATUSES = (TaskStatus.BLOCKED, TaskStatus.SKIPPED)
"""Work that is unfinished but withheld — a blocker (automatic or manual) or a pass.
Held work is NOT terminal: it still owes a result, so a prerequisite in one of these
states keeps its dependents waiting. It is simply not startable right now."""

TERMINAL_STATUSES = (TaskStatus.DONE, TaskStatus.CANCELLED)
"""Work that owes nobody anything, so it satisfies a dependency. `SKIPPED` is
deliberately absent — see `HELD_STATUSES` and `workflows.materialize.is_resolved`."""

STATUS_PARTITION = (STARTABLE_STATUSES, HELD_STATUSES, TERMINAL_STATUSES)
"""The three-way partition of `TaskStatus`: exhaustive and disjoint by construction,
asserted in `checks/runtime/test_task_dependency_state.py`. A new status must land in
exactly one of the three, which is what stops a scheduler from silently inheriting it."""


class TaskPriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TRIVIAL = "trivial"

    @classmethod
    def normalize(cls, value: Any) -> "TaskPriority":
        return (
            value
            if isinstance(value, cls)
            else _enum_value(cls, str(value).strip().lower(), cls.MEDIUM)
        )


class DependencyType(str, enum.Enum):
    BLOCKS = "BLOCKS"
    REQUIRED_FOR = "REQUIRED_FOR"


@dataclass
class TaskDependency:
    depends_on_task_id: str
    dependency_type: DependencyType = DependencyType.BLOCKS

    def to_dict(self) -> dict[str, Any]:
        return dict(
            depends_on_task_id=self.depends_on_task_id,
            dependency_type=self.dependency_type.value,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskDependency":
        kind = _enum_value(
            DependencyType,
            d.get("dependency_type", DependencyType.BLOCKS.value),
            DependencyType.BLOCKS,
        )
        return cls(str(d.get("depends_on_task_id", "")), kind)


class ExitCriteriaStatus(str, enum.Enum):
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"


def normalize_exit_criterion(item: Any) -> dict:
    mapping = item if isinstance(item, dict) else {}
    description = (
        item
        if isinstance(item, str)
        else str(mapping.get("description") or mapping.get("criteria") or "")
    )
    complete = (
        str(mapping["status"]).strip().lower()
        in ("complete", "completed", "done", "true", "met")
        if "status" in mapping
        else bool(mapping.get("met"))
    )
    return {
        "description": description,
        "status": "complete" if complete else "incomplete",
        "comment": str(mapping.get("comment") or ""),
        "met": complete,
    }


def normalize_action_plan_item(item: Any, index: int) -> dict:
    mapping = item if isinstance(item, dict) else {}
    content = (
        item
        if isinstance(item, str)
        else str(mapping.get("content") or mapping.get("description") or "")
    )
    try:
        sequence = int(mapping.get("sequence", index))
    except (TypeError, ValueError):
        sequence = index
    return {
        "sequence": sequence,
        "content": content,
        "description": content,
        "completed": bool(mapping.get("completed")),
    }


def _as_item_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, (str, dict)):
        return [value]
    try:
        return list(value) if value is not None else []
    except TypeError:
        return []


def normalize_note(item: Any) -> dict:
    mapping = item if isinstance(item, dict) else {}
    result = {
        "content": item if isinstance(item, str) else str(mapping.get("content") or ""),
        "timestamp": str(mapping.get("timestamp") or mapping.get("created_at") or ""),
    }
    if mapping.get("phase"):
        result["phase"] = mapping["phase"]
    return result


@dataclass
class WorkflowTaskBinding:
    run_id: str
    node_id: str
    node_path: str = ""
    managed: bool = True
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in ("run_id", "node_id", "node_path", "managed", "fingerprint")
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowTaskBinding":
        row = d or {}
        values = {
            key: str(row.get(key, "") or "")
            for key in ("run_id", "node_id", "node_path", "fingerprint")
        }
        return cls(**values, managed=bool(row.get("managed", True)))


@dataclass
class Task:
    id: str
    title: str
    status: "TaskStatus" = TaskStatus.OPEN
    description: str = ""
    provider: str = ""
    project: str = ""
    task_list_id: str = ""
    dependencies: list[TaskDependency] = field(default_factory=list)
    author: str = ""
    origin_harness: str = ""
    assignee: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    labels: list[str] = field(default_factory=list)
    due: str = ""
    order: float = 0.0
    exit_criteria: list[dict] = field(default_factory=list)
    action_plan: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    research_notes: list[dict] = field(default_factory=list)
    execution_notes: list[dict] = field(default_factory=list)
    agent_instructions_template: str = ""
    blocked_reason_kind: str = ""
    workflow_binding: "WorkflowTaskBinding | None" = None
    blocked_kind: str = ""
    preview: str = ""
    done_criterion: str = ""
    evidence: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        for name in ("exit_criteria", "action_plan"):
            setattr(self, name, _as_item_list(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return TaskDocument.encode(self)

    def belongs_to(self, username: str) -> bool:
        owner = (username or "").strip().lower()
        if not owner:
            return True
        assignee = (self.assignee or "").strip().lower()
        if assignee:
            return assignee == owner
        author = (self.author or "").strip().lower()
        return not author or author == owner

    def can_mark_complete(self) -> bool:
        return all(
            normalize_exit_criterion(value)["met"] for value in self.exit_criteria
        )

    def incomplete_exit_criteria(self) -> list[str]:
        normalized = map(normalize_exit_criterion, self.exit_criteria)
        return [row["description"] for row in normalized if not row["met"]]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(**TaskDocument.decode(d))

    def prerequisite_ids(self) -> list[str]:
        return [
            edge.depends_on_task_id
            for edge in self.dependencies
            if edge.dependency_type == DependencyType.BLOCKS and edge.depends_on_task_id
        ]


class TaskDocument:
    @staticmethod
    def encode(task):
        result = asdict(task)
        result.update(
            status=task.status.value,
            priority=task.priority.value,
            dependencies=[edge.to_dict() for edge in task.dependencies],
            workflow_binding=(
                None
                if task.workflow_binding is None
                else task.workflow_binding.to_dict()
            ),
        )
        result["exit_criteria"] = list(
            map(normalize_exit_criterion, task.exit_criteria)
        )
        result["action_plan"] = [
            normalize_action_plan_item(value, index)
            for index, value in enumerate(task.action_plan)
        ]
        for name in ("notes", "research_notes", "execution_notes"):
            result[name] = list(map(normalize_note, getattr(task, name)))
        return result

    @staticmethod
    def decode(row):
        values = {item.name: row.get(item.name) for item in fields(Task)}
        values["status"] = row.get("status", "open")
        values["priority"] = row.get("priority", "medium")
        values["dependencies"] = row.get("dependencies") or row.get("depends_on")
        return {
            name: coerce_task_field(name, value, strict=False)
            for name, value in values.items()
        }


@dataclass
class TaskComment:
    id: str
    task_id: str
    author: str
    body: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BUILTIN_PROJECTS = ("Personal", "Repeatable")


@dataclass(frozen=True)
class FieldAdmission:
    strict: bool

    def refuse(self, message, default):
        if self.strict:
            raise ValueError(message)
        return default


def _as_text(value: Any, *, strict: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return FieldAdmission(strict).refuse(
        f"expected text, got {type(value).__name__}", ""
    )


def _as_number(value: Any, *, strict: bool) -> float:
    if value is None or value == "":
        return 0.0
    admission = FieldAdmission(strict)
    if isinstance(value, bool):
        return admission.refuse("expected a number, got a boolean", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return admission.refuse(f"expected a number, got {value!r}", 0.0)


def _as_text_list(value: Any, *, strict: bool) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [_as_text(value, strict=strict)]
    admission = FieldAdmission(strict)
    if isinstance(value, dict):
        return admission.refuse("expected a list of text, got an object", [])
    try:
        items = list(value)
    except TypeError:
        return admission.refuse(
            f"expected a list of text, got {type(value).__name__}", []
        )
    return [_as_text(item, strict=strict) for item in items]


def _as_dict_list(normalizer: Any, *, indexed: bool = False) -> Any:
    def normalize(value, *, strict):
        return [
            normalizer(item, index) if indexed else normalizer(item)
            for index, item in enumerate(_as_item_list(value))
        ]

    return normalize


def _as_open_dict_list(value: Any, *, strict: bool) -> list[dict]:
    rows = _as_item_list(value)
    result = []
    for row in rows:
        if isinstance(row, dict):
            result.append(row)
        else:
            FieldAdmission(strict).refuse(
                f"expected a list of objects, got {type(row).__name__}", None
            )
    return result


def _as_dependencies(value: Any, *, strict: bool) -> list["TaskDependency"]:
    if value is None:
        return []
    try:
        items = [value] if isinstance(value, (str, dict)) else list(value)
    except TypeError:
        return FieldAdmission(strict).refuse("expected a list of dependencies", [])
    edges = []
    for item in items:
        if isinstance(item, TaskDependency):
            edges.append(item)
        elif isinstance(item, dict):
            edges.append(TaskDependency.from_dict(item))
        elif isinstance(item, str) and item.strip():
            edges.append(TaskDependency(item, DependencyType.BLOCKS))
        else:
            FieldAdmission(strict).refuse(f"not a dependency: {item!r}", None)
    return edges


def _as_status(value: Any, *, strict: bool) -> "TaskStatus":
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError:
        message = f"invalid status {value!r} — use one of: " + ", ".join(
            status.value for status in TaskStatus
        )
        return FieldAdmission(strict).refuse(message, TaskStatus.OPEN)


def _as_priority(value: Any, *, strict: bool) -> "TaskPriority":
    return TaskPriority.normalize(value)


def _as_binding(value: Any, *, strict: bool) -> "WorkflowTaskBinding | None":
    if value is None or isinstance(value, WorkflowTaskBinding):
        return value
    if isinstance(value, dict):
        return WorkflowTaskBinding.from_dict(value)
    return FieldAdmission(strict).refuse(
        "workflow_binding must be an object or null", None
    )


TASK_FIELD_COERCERS: dict[str, Any] = {
    "id": _as_text,
    "title": _as_text,
    "status": _as_status,
    "description": _as_text,
    "provider": _as_text,
    "project": _as_text,
    "task_list_id": _as_text,
    "dependencies": _as_dependencies,
    "author": _as_text,
    "origin_harness": _as_text,
    "assignee": _as_text,
    "priority": _as_priority,
    "labels": _as_text_list,
    "due": _as_text,
    "order": _as_number,
    "exit_criteria": _as_dict_list(normalize_exit_criterion),
    "action_plan": _as_dict_list(normalize_action_plan_item, indexed=True),
    "notes": _as_dict_list(normalize_note),
    "research_notes": _as_dict_list(normalize_note),
    "execution_notes": _as_dict_list(normalize_note),
    "agent_instructions_template": _as_text,
    "blocked_reason_kind": _as_text,
    "workflow_binding": _as_binding,
    "blocked_kind": _as_text,
    "preview": _as_text,
    "done_criterion": _as_text,
    "evidence": _as_open_dict_list,
    "attempts": _as_open_dict_list,
    "created_at": _as_text,
    "updated_at": _as_text,
    "url": _as_text,
}


def coerce_task_field(name: str, value: Any, *, strict: bool = True) -> Any:
    if name not in TASK_FIELD_COERCERS or TASK_FIELD_COERCERS[name] is None:
        raise ValueError(f"unknown task field {name!r}")
    return TASK_FIELD_COERCERS[name](value, strict=strict)


@dataclass
class Project:
    id: str
    name: str
    is_builtin: bool = False
    status: str = "active"
    workspace_dir: str = ""
    name_locked: bool = False
    agent_instructions_template: str = ""
    brief: str = ""
    origin_harness: str = ""
    created_at: str = ""
    updated_at: str = ""

    def is_builtin_project(self) -> bool:
        return self.is_builtin or self.name in BUILTIN_PROJECTS

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "is_builtin": self.is_builtin_project()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        raw = {
            name: d.get(name, "")
            for name in (
                "id",
                "name",
                "agent_instructions_template",
                "created_at",
                "updated_at",
            )
        }
        strings = {
            name: str(d.get(name) or "")
            for name in ("workspace_dir", "brief", "origin_harness")
        }
        return cls(
            **raw,
            **strings,
            status=str(d.get("status") or "active"),
            name_locked=bool(d.get("name_locked", False)),
            is_builtin=bool(d.get("is_builtin", d.get("is_default", False)))
            or raw["name"] in BUILTIN_PROJECTS,
        )


@dataclass
class TaskList:
    id: str
    name: str
    project_id: str
    agent_instructions_template: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskList":
        return cls(
            **{
                name: d.get(name, "")
                for name in (
                    "id",
                    "name",
                    "project_id",
                    "agent_instructions_template",
                    "created_at",
                    "updated_at",
                )
            }
        )
