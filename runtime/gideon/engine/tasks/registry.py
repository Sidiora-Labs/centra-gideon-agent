"""Task source routing, aggregated queries and admission-backed ready views."""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from gideon.engine.tasks import reconcile
from gideon.engine.tasks.models import Task, TaskComment, TaskPriority
from gideon.engine.tasks.provider import TaskProvider

logger = logging.getLogger(__name__)
_providers: dict[str, TaskProvider] = {}


def register_provider(provider: TaskProvider) -> None:
    _providers.update({provider.name: provider})


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> TaskProvider | None:
    return _providers.get(name)


def list_providers() -> list[str]:
    return list(_providers)


def _ensure_native() -> None:
    if "native" not in _providers:
        from gideon.engine.tasks.native import NativeTaskProvider

        register_provider(NativeTaskProvider())


@dataclass(frozen=True)
class TaskDirectory:
    providers: dict[str, TaskProvider]

    def selected(self, name):
        return self.providers.get(name) if name else None

    async def find(self, identifier):
        for provider in self.providers.values():
            record = await provider.get_task(identifier)
            if record:
                return provider, record
        return None, None

    async def owner(self, identifier, name):
        selected = self.selected(name)
        if selected is not None:
            return selected
        return (await self.find(identifier))[0]

    async def collect(self, name, fields):
        selected = self.selected(name)
        sources = [selected] if selected is not None else self.providers.values()
        records = []
        for provider in sources:
            try:
                rows, _ = await provider.list_tasks(**fields, limit=500, offset=0)
                records.extend(rows)
            except Exception:
                logger.warning(
                    "Task provider %s failed to list", provider.name, exc_info=True
                )
        return records

    @staticmethod
    def writable(provider):
        if provider.readonly:
            raise ValueError(f"Provider '{provider.name}' is read-only")
        return provider


def _directory():
    _ensure_native()
    return TaskDirectory(_providers)


async def list_all_tasks(
    status: str | None = None,
    assignee: str | None = None,
    project: str | None = None,
    task_list_id: str | None = None,
    provider_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    rows = await _directory().collect(
        provider_filter, {"status": status, "assignee": assignee, "project": project}
    )
    selected = [
        row for row in rows if not task_list_id or row.task_list_id == task_list_id
    ]
    selected.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
    return selected[offset : offset + limit], len(selected)


async def get_task(task_id: str, provider_name: str | None = None) -> Task | None:
    sources = _directory()
    provider = sources.selected(provider_name)
    return (
        await provider.get_task(task_id)
        if provider is not None
        else (await sources.find(task_id))[1]
    )


async def create_task(provider_name: str = "native", **fields: Any) -> Task:
    sources = _directory()
    provider = sources.selected(provider_name)
    if provider is None:
        raise ValueError(f"Unknown task provider: {provider_name}")
    return await sources.writable(provider).create_task(**fields)


async def update_task(
    task_id: str, provider_name: str | None = None, **fields: Any
) -> Task | None:
    sources = _directory()
    provider = await sources.owner(task_id, provider_name)
    return (
        None
        if provider is None
        else await sources.writable(provider).update_task(task_id, **fields)
    )


async def delete_task(task_id: str, provider_name: str | None = None) -> bool:
    sources = _directory()
    provider = await sources.owner(task_id, provider_name)
    return (
        False
        if provider is None
        else await sources.writable(provider).delete_task(task_id)
    )


async def get_comments(
    task_id: str, provider_name: str | None = None
) -> list[TaskComment]:
    provider = await _directory().owner(task_id, provider_name)
    return [] if provider is None else await provider.get_comments(task_id)


async def add_comment(
    task_id: str, body: str, author: str = "", provider_name: str | None = None
) -> TaskComment | None:
    provider = await _directory().owner(task_id, provider_name)
    return (
        None if provider is None else await provider.add_comment(task_id, body, author)
    )


async def delete_comment(
    task_id: str, comment_id: str, provider_name: str | None = None
) -> bool:
    sources = _directory()
    provider = await sources.owner(task_id, provider_name)
    return (
        False
        if provider is None
        else await sources.writable(provider).delete_comment(task_id, comment_id)
    )


async def task_graph(provider_filter: str | None = None) -> dict[str, Any]:
    sources = _directory()
    provider = sources.selected(provider_filter or "native")
    if provider is None or not hasattr(provider, "graph"):
        provider = sources.providers["native"]
    return await asyncio.to_thread(provider.graph)


def _iso_to_epoch(text: str) -> float:
    value = (text or "").strip()
    if value:
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return 0.0


@dataclass(frozen=True)
class ReadyProjection:
    ready: list[Task]
    tasks: dict[str, Task]
    now: float

    def rank(self):
        from gideon.automation.workflows import admission, pool

        counts = Counter(
            prerequisite
            for task in self.tasks.values()
            for prerequisite in task.prerequisite_ids()
        )
        leases = {
            task.id: lease
            for task in self.ready
            if (lease := pool.read_lease(task.id)) is not None
        }
        state = admission.AdmissionState(
            now=self.now, holder=admission.OBSERVER, leases=leases
        )
        policies = admission.default_policies(
            admission.Limits(), single_active_feature=False, state=state
        )
        candidates = [
            admission.ReadyItem(
                item_id=task.id,
                title=task.title,
                priority=task.priority.value,
                blocks_count=counts.get(task.id, 0),
                overdue=bool(task.due) and 0.0 < _iso_to_epoch(task.due) <= self.now,
                updated_at=_iso_to_epoch(task.updated_at),
            )
            for task in self.ready
        ]
        records = {task.id: task for task in self.ready}
        return [records[item.item_id] for item in admission.ready(candidates, policies)]


def _rank_ready(
    ready: list[Task], task_map: dict[str, Task], *, now: float
) -> list[Task]:
    return ReadyProjection(ready, task_map, now).rank()


async def ready_tasks(
    project: str | None = None,
    task_list_id: str | None = None,
    *,
    mine_only: bool = True,
) -> list[Task]:
    records, _ = await list_all_tasks(
        project=project, task_list_id=task_list_id, limit=10_000
    )
    graph = {row.id: row for row in records}
    ready_ids = set(reconcile.ready_task_ids(graph))
    selected = [row for row in records if row.id in ready_ids]
    if mine_only:
        from gideon.cognition.identity import current_username

        owner = current_username()
        if owner:
            selected = [row for row in selected if row.belongs_to(owner)]
    return await asyncio.to_thread(_rank_ready, selected, graph, now=time.time())


@dataclass(frozen=True)
class TaskSearch:
    text: str
    statuses: set
    priorities: set
    tags: set

    def score(self, task):
        if self.statuses and task.status.value not in self.statuses:
            return None
        if self.priorities and task.priority.value not in self.priorities:
            return None
        if self.tags and not self.tags.intersection(task.labels):
            return None
        if not self.text:
            return 0.0
        score = 2.0 * task.title.lower().count(self.text) + (
            task.description or ""
        ).lower().count(self.text)
        return score if score > 0 else None

    def ordered(self, tasks, sort_by):
        matches = [
            (score, task) for task in tasks if (score := self.score(task)) is not None
        ]
        weights = {
            TaskPriority.CRITICAL: 5,
            TaskPriority.HIGH: 4,
            TaskPriority.MEDIUM: 3,
            TaskPriority.LOW: 2,
            TaskPriority.TRIVIAL: 1,
        }
        if sort_by == "relevance" and self.text:
            key = lambda pair: (pair[0], pair[1].created_at)
        elif sort_by == "priority":
            key = lambda pair: (weights.get(pair[1].priority, 3), pair[1].created_at)
        elif sort_by == "created_at":
            key = lambda pair: pair[1].created_at
        else:
            key = lambda pair: pair[1].updated_at or pair[1].created_at
        matches.sort(key=key, reverse=True)
        return [task for _, task in matches]


async def search_tasks(
    query: str = "",
    statuses: list[str] | None = None,
    priorities: list[str] | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    task_list_id: str | None = None,
    sort_by: str = "relevance",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    rows, _ = await list_all_tasks(
        project=project, task_list_id=task_list_id, limit=10_000
    )
    search = TaskSearch(
        (query or "").strip().lower(),
        set(filter(None, statuses or [])),
        set(filter(None, priorities or [])),
        set(filter(None, tags or [])),
    )
    matched = search.ordered(rows, sort_by)
    return matched[offset : offset + limit], len(matched)
