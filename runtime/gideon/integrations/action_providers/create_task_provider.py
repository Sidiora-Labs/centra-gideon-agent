"""Create persistent tasks and reverse them through their originating provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.template import render_template


@dataclass(frozen=True)
class _TaskHandle:
    provider: str
    task_id: str

    @classmethod
    def parse(cls, handle: str) -> _TaskHandle | None:
        parts = handle.split(":", 2)
        if len(parts) != 3 or parts[0] != "task" or not all(parts[1:]):
            return None
        return cls(parts[1], parts[2])

    def encoded(self) -> str:
        return f"task:{self.provider}:{self.task_id}" if self.task_id else ""

    async def remove(self) -> ActionResult:
        try:
            from gideon.engine.tasks.registry import delete_task, get_task

            if await get_task(self.task_id, self.provider) is None:
                return ActionResult(
                    False,
                    error=f"task {self.task_id} is already gone — nothing left to undo",
                )
            removed = await delete_task(self.task_id, self.provider)
        except Exception as error:
            return ActionResult(
                False, error=f"could not delete task {self.task_id}: {error}"
            )
        if removed:
            return ActionResult(True, stdout=f"deleted task {self.task_id}")
        return ActionResult(False, error=f"task {self.task_id} could not be deleted")


@dataclass(frozen=True)
class _TaskCreation:
    provider: str
    fields: dict[str, Any]

    @classmethod
    def prepare(
        cls, config: dict[str, Any], ctx: ActionContext, title: str
    ) -> _TaskCreation:
        provider = (config.get("provider") or "native").strip() or "native"
        fields: dict = {"title": title}
        body = render_template(config.get("body_template", ""), ctx)
        if body:
            fields.update(description=body)
        fields.update(
            (key, config[key])
            for key in ("priority", "project", "assignee", "due")
            if config.get(key)
        )
        labels = config.get("labels")
        if isinstance(labels, list) and labels:
            fields.update(labels=labels)
        return cls(provider, fields)

    async def create(self) -> ActionResult:
        try:
            from gideon.engine.tasks.registry import create_task

            record = await create_task(self.provider, **self.fields)
        except Exception as error:
            return ActionResult(False, error=f"create-task failed: {error}")
        handle = _TaskHandle(self.provider, str(getattr(record, "id", "") or ""))
        return ActionResult(
            True,
            stdout=f"created task {handle.task_id or '?'}: {self.fields['title'][:80]}",
            reversal=handle.encoded(),
        )


class CreateTaskActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "create-task"

    @property
    def display_name(self) -> str:
        return "Create Task"

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        return ("task",)

    async def reverse(self, handle: str) -> ActionResult:
        parsed = _TaskHandle.parse(handle)
        if parsed is not None:
            return await parsed.remove()
        return ActionResult(False, error=f"not a create-task handle: {handle!r}")

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        title = render_template(action_config.get("title_template", ""), ctx).strip()
        if not title:
            return ActionResult(
                False, error="create-task hook is missing 'title_template'"
            )
        return await _TaskCreation.prepare(action_config, ctx, title).create()


def create_provider(config: dict[str, Any] | None = None) -> CreateTaskActionProvider:
    return CreateTaskActionProvider()
