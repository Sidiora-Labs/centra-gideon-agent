"""Adapt sandboxed script receipts to the native action outcome vocabulary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import (
    ActionClock,
    action_timeout,
)

_SCRIPT_OUTCOMES = {"ok": "", "report": "", "done": "done", "skip": "skip"}


@dataclass(frozen=True)
class _ScriptAction:
    reference: str
    event: str
    message: str
    timeout: int

    async def run(self) -> ActionResult:
        from gideon.automation.schedule_script import run_script_sandboxed

        clock = ActionClock()
        executor = asyncio.get_running_loop()
        try:
            receipt = await executor.run_in_executor(
                None,
                run_script_sandboxed,
                self.reference,
                f"action:{self.event}",
                self.message,
                self.timeout,
            )
        except Exception as error:
            return clock.result(False, error=str(error))
        status = receipt.get("status")
        if status in tuple(_SCRIPT_OUTCOMES):
            return clock.result(
                True,
                stdout=receipt.get("message", "") or "",
                outcome=_SCRIPT_OUTCOMES[status],
            )
        return clock.result(
            False, error=receipt.get("error") or f"script status {status!r}"
        )


class RunScriptActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "run-script"

    @property
    def display_name(self) -> str:
        return "Run Script"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        reference = (action_config.get("script") or "").strip()
        if not reference:
            return ActionResult(
                False, error="run-script action is missing 'script' field"
            )
        invocation = _ScriptAction(
            reference, ctx.event, ctx.context, action_timeout(action_config, timeout)
        )
        return await invocation.run()


def create_provider(config: dict[str, Any] | None = None) -> RunScriptActionProvider:
    return RunScriptActionProvider()
