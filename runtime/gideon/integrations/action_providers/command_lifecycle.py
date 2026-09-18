"""Timing and owned process cleanup for native command actions."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from gideon.core.cancellation import run_with_timeout, terminate_and_reap
from gideon.integrations.action_providers.base import ActionResult


def action_timeout(config: dict[str, Any], fallback: int) -> int:
    try:
        declared = int(config.get("timeout", 0) or 0)
    except (TypeError, ValueError):
        declared = 0
    return declared or fallback


@dataclass(frozen=True)
class ActionClock:
    started: float = field(default_factory=time.monotonic)

    def result(self, success: bool, **details: Any) -> ActionResult:
        details["duration_ms"] = int(1000 * (time.monotonic() - self.started))
        return ActionResult(success=success, **details)


@dataclass
class CommandProcess:
    cleanup_path: str | None
    process: asyncio.subprocess.Process | None = None
    completed: bool = False

    async def capture(self, payload: bytes, timeout: int) -> tuple[bytes, bytes]:
        output = await run_with_timeout(self.process, timeout, payload=payload)
        self.completed = True
        return output

    async def stop(self) -> None:
        """Retire the owned child through the one process-retirement owner.

        ``run_with_timeout`` already retires a child that blew its deadline; this is
        the path for every OTHER way the action ends early (a refusal, a raised
        provider error) and it must not re-implement the group kill, the escalation,
        or the pipe close — they live in :mod:`gideon.core.cancellation`.
        """
        process = self.process
        if process is None or self.completed:
            return
        await terminate_and_reap(process)
        self.completed = True

    async def close(self) -> None:
        try:
            await self.stop()
        finally:
            if self.cleanup_path:
                try:
                    os.unlink(self.cleanup_path)
                except OSError:
                    pass
