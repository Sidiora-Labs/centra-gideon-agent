"""Task dependency walks, automatic status transitions and graph projections."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, fields

from gideon.engine.tasks.models import TERMINAL_STATUSES, Task, TaskStatus


class DependencyCycleError(ValueError):
    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("dependency cycle detected: " + " → ".join(cycle))


def _prereq_map(tasks: dict[str, Task]) -> dict[str, list[str]]:
    return {
        key: list(filter(tasks.__contains__, row.prerequisite_ids()))
        for key, row in tasks.items()
    }


def _dependents_map(tasks: dict[str, Task]) -> dict[str, list[str]]:
    reverse: dict = {key: [] for key in tasks}
    for dependent, prerequisites in _prereq_map(tasks).items():
        for prerequisite in prerequisites:
            reverse[prerequisite].append(dependent)
    return reverse


@dataclass(frozen=True)
class DependencyWalk:
    edges: dict[str, list[str]]

    def visit(self, starts):
        completed, order = set(), []
        for start in starts:
            if start in completed:
                continue
            path, active = [start], {start: 0}
            stack = [iter(self.edges.get(start, []))]
            while stack:
                neighbor = next(stack[-1], None)
                if neighbor is None:
                    node = path.pop()
                    stack.pop()
                    active.pop(node)
                    completed.add(node)
                    order.append(node)
                elif neighbor in active:
                    return path[active[neighbor] :] + [neighbor], order
                elif neighbor not in completed:
                    active[neighbor] = len(path)
                    path.append(neighbor)
                    stack.append(iter(self.edges.get(neighbor, [])))
        return [], order


def detect_cycle(tasks: dict[str, Task]) -> list[str]:
    return DependencyWalk(_prereq_map(tasks)).visit(tasks)[0]


def would_create_cycle(
    tasks: dict[str, Task], task_id: str, new_prereq_ids: list[str]
) -> list[str]:
    proposed = _prereq_map(tasks)
    proposed[task_id] = [key for key in new_prereq_ids if key in tasks]
    return DependencyWalk(proposed).visit([task_id])[0]


def _unfinished(task, tasks):
    return [
        key
        for key in task.prerequisite_ids()
        if key in tasks and tasks[key].status not in TERMINAL_STATUSES
    ]


def block_reason(task: Task, tasks: dict[str, Task]) -> dict:
    identifiers = _unfinished(task, tasks)
    titles = [tasks[key].title for key in identifiers]
    return {
        "is_blocked": bool(identifiers),
        "blocking_task_ids": identifiers,
        "blocking_task_titles": titles,
        "message": "Waiting on: " + ", ".join(titles) if identifiers else "",
    }


@dataclass(frozen=True)
class BlockTransition:
    task: Task
    unfinished: bool

    def apply(self):
        row = self.task
        if row.blocked_reason_kind == "manual":
            return False
        if self.unfinished and row.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS):
            row.status, row.blocked_reason_kind = TaskStatus.BLOCKED, "auto"
            return True
        if (
            not self.unfinished
            and row.status == TaskStatus.BLOCKED
            and row.blocked_reason_kind == "auto"
        ):
            row.status, row.blocked_reason_kind = TaskStatus.OPEN, ""
            return True
        return False


def reconcile_blocked_status(tasks: dict[str, Task], changed_id: str) -> list[Task]:
    if changed_id not in tasks:
        return []
    dependents = _dependents_map(tasks)
    pending, seen, affected = deque([changed_id]), set(), []
    while pending:
        key = pending.popleft()
        if key not in seen:
            seen.add(key)
            affected.append(key)
            pending.extend(dependents.get(key, []))
    changed = []
    for key in affected:
        task = tasks[key]
        if BlockTransition(task, bool(_unfinished(task, tasks))).apply():
            changed.append(task)
    return changed


def classify_manual_block(task: Task, tasks: dict[str, Task]) -> None:
    if task.status == TaskStatus.BLOCKED:
        task.blocked_reason_kind = "auto" if _unfinished(task, tasks) else "manual"
    elif task.blocked_reason_kind == "manual":
        task.blocked_reason_kind = ""


def ready_task_ids(tasks: dict[str, Task]) -> list[str]:
    return [
        key
        for key, task in tasks.items()
        if task.status not in TERMINAL_STATUSES and not _unfinished(task, tasks)
    ]


@dataclass
class DependencyAnalysis:
    completion_pct: float = 0.0
    leaf_task_ids: list[str] = field(default_factory=list)
    root_task_ids: list[str] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    bottleneck_tasks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            name: round(value, 1) if name == "completion_pct" else value
            for descriptor in fields(self)
            for name, value in [(descriptor.name, getattr(self, descriptor.name))]
        }


@dataclass(frozen=True)
class TaskGraphView:
    tasks: dict[str, Task]
    prerequisites: dict[str, list[str]]
    dependents: dict[str, list[str]]

    def critical(self, order):
        lengths, predecessor = {}, {}
        for key in order:
            options = self.prerequisites.get(key, [])
            best = max(options, key=lengths.__getitem__) if options else None
            lengths[key] = 1 + (lengths[best] if best is not None else 0)
            predecessor[key] = best
        current = max(self.tasks, key=lengths.__getitem__)
        path = []
        while current is not None:
            path.append(current)
            current = predecessor[current]
        path.reverse()
        return path

    def analyze(self):
        cycle, order = DependencyWalk(self.prerequisites).visit(self.tasks)
        counts = sorted(
            self.dependents, key=lambda key: len(self.dependents[key]), reverse=True
        )
        return DependencyAnalysis(
            completion_pct=100.0
            * sum(row.status == TaskStatus.DONE for row in self.tasks.values())
            / len(self.tasks),
            leaf_task_ids=[key for key in self.tasks if not self.dependents.get(key)],
            root_task_ids=[
                key for key in self.tasks if not self.prerequisites.get(key)
            ],
            critical_path=[] if cycle else self.critical(order),
            cycles=[cycle] if cycle else [],
            bottleneck_tasks=[
                {"id": key, "dependents": len(self.dependents[key])}
                for key in counts
                if len(self.dependents[key]) >= 2
            ],
        )


def analyze(tasks: dict[str, Task]) -> DependencyAnalysis:
    if not tasks:
        return DependencyAnalysis()
    return TaskGraphView(tasks, _prereq_map(tasks), _dependents_map(tasks)).analyze()
