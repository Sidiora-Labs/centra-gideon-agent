"""Project selection, unique naming and shared task-list binding."""

from __future__ import annotations

import logging

from gideon.engine.tasks.hierarchy import HierarchyStore

logger = logging.getLogger(__name__)
DEFAULT_PROJECT_NAME = "Personal"


def _store() -> HierarchyStore:
    return HierarchyStore()


class ProjectNames:
    def __init__(self, store):
        self.store = store

    def available(self, base, *, own_id=None):
        candidate, number = base, 1
        while True:
            existing = self.store.get_project_by_name(candidate)
            if existing is None or own_id is not None and existing.id == own_id:
                return candidate
            number += 1
            suffix = f" ({number})"
            candidate = base[: 80 - len(suffix)] + suffix

    def create(self, name):
        base = name[:80].strip() or DEFAULT_PROJECT_NAME
        return self.store.create_project(self.available(base))

    def rename(self, project_id, title):
        project = self.store.get_project(project_id)
        if (
            project is None
            or project.is_builtin_project()
            or project.name_locked
            or project.name == title
        ):
            return
        target = self.available(title, own_id=project_id)
        try:
            self.store.update_project(project_id, name=target)
        except ValueError:
            logger.debug("maybe_rename_from: rename to %r skipped (collision)", target)


def resolve_project_id(chosen_id: str = "", *, auto_name: str = "") -> str:
    store = _store()
    existing = store.get_project(chosen_id) if chosen_id else None
    if existing is not None:
        return existing.id
    name = (auto_name or "").strip()
    selected = (
        _create_unique(store, name)
        if name
        else store.find_or_create_project(DEFAULT_PROJECT_NAME)
    )
    return selected.id


def _create_unique(store: HierarchyStore, name: str):
    return ProjectNames(store).create(name)


def ensure_task_list(project_id: str, list_name: str) -> str:
    store = _store()
    existing = next(
        (
            row
            for row in store.list_task_lists(project_id=project_id)
            if row.name == list_name
        ),
        None,
    )
    return (
        existing.id
        if existing is not None
        else store.create_task_list(name=list_name, project_id=project_id).id
    )


def context_dir(project_id: str) -> str:
    if project_id:
        store = _store()
        if store.get_project(project_id) is not None:
            return str(store.context_dir(project_id))
    return ""


def maybe_rename_from(project_id: str, title: str) -> None:
    normalized = (title or "").strip()[:80]
    if normalized:
        ProjectNames(_store()).rename(project_id, normalized)
