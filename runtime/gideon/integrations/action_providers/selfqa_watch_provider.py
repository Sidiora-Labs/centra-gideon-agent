"""Turn a commit-watch verdict into an existing workflow action invocation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _WatchDispatch:
    fire: Any
    template: str

    async def start(self, ctx: ActionContext, timeout: int) -> ActionResult:
        from gideon.integrations.action_providers.registry import get_action_provider

        if self.fire.inputs is None:
            return ActionResult(True, stdout=f"skipped: {self.fire.quiet_reason}")
        runner = get_action_provider("run-workflow")
        if runner is None:
            return ActionResult(
                False,
                error="run-workflow provider unavailable",
                stderr="selfqa-commit-watch delegates the start; nothing can start runs",
            )
        action = dict(
            workflow=self.template,
            inputs=self.fire.inputs,
            idempotency_key=self.fire.idempotency_key,
        )
        result = await runner.execute(action, ctx, timeout=timeout)
        if result.success:
            logger.info(
                "selfqa: commit watch started %s for %d commit(s)",
                self.template,
                len(self.fire.inputs.get("commits", [])),
            )
        return result


class SelfQaCommitWatchActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-commit-watch"

    @property
    def display_name(self) -> str:
        return "Self-QA commit watch"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.assurance.selfqa import watch

        repository = str((action_config or {}).get("repo") or "")
        dispatch = _WatchDispatch(watch.check(repository), watch.TEMPLATE)
        return await dispatch.start(ctx, timeout)
