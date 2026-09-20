"""Task write rules shared by user-facing write doors."""

from typing import Any

ENGINE_OWNED_FIELDS = frozenset(
    {"status", "blocked_kind", "preview", "done_criterion", "evidence", "attempts"}
)


def managed(task: Any) -> bool:
    binding = getattr(task, "workflow_binding", None)
    return bool(binding is not None and binding.managed)


def reject_write(task: Any, fields: dict[str, Any]) -> str:
    if not managed(task):
        return ""
    attempted = sorted(set(fields or {}) & ENGINE_OWNED_FIELDS)
    if not attempted:
        return ""
    binding = getattr(task, "workflow_binding", None)
    run_id = getattr(binding, "run_id", "") or "the owning run"
    return (
        f"{', '.join(attempted)} on this task {'is' if len(attempted) == 1 else 'are'} driven by "
        f"run {run_id} — use workflow_skip or workflow_rewind to change what the run does, and the "
        "task will follow. A direct write would make the board disagree with the run it shows."
    )


async def resolve_reject_write(
    task_id: str, fields: dict[str, Any], *, provider_name: str | None = None
) -> str:
    from gideon.engine.tasks import registry

    task = await registry.get_task(task_id, provider_name=provider_name)
    return reject_write(task, fields) if task is not None else ""
